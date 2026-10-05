"""意图解析：日常查询规则优先（零成本），其余走 LLM 分三类。

流程：match_daily_query 规则拦截"天气/周几/倒计时"→ 未命中再调 LLM
解析为 research / hotlist / chat。
"""
import re

from .config import CITY_CODES
from .facts import KNOWN_FOREIGN, extract_city
from .llm import call_llm_json

# ---- 规则层：日常查询 ----

WEATHER_KEYWORDS = ("天气", "下雨", "下雪", "降雨", "气温", "温度", "降温", "雨", "雪", "晴")
WEEKDAY_KEYWORDS = ("周几", "星期几", "礼拜几")


def match_daily_query(text: str) -> dict | None:
    """规则匹配日常查询。命中返回 {intent, ...}，否则返回 None。"""
    t = text.strip()

    # 天气类（含"明天会下雨吗"）
    if any(w in t for w in WEATHER_KEYWORDS):
        day = "今天"
        if "后天" in t:
            day = "后天"
        elif "昨天" in t:
            day = "昨天"
        elif "明天" in t:
            day = "明天"
        city, city_kind = extract_city(t)
        return {
            "intent": "weather",
            "day": day,
            "city": city,
            "city_kind": city_kind,
            "ask_rain": any(w in t for w in ("下雨", "下雪", "降雨", "雨", "雪")),
        }

    # 周几类
    if any(w in t for w in WEEKDAY_KEYWORDS):
        day = "明天"
        if "今天" in t:
            day = "今天"
        elif "后天" in t:
            day = "后天"
        return {"intent": "weekday", "day": day}

    # 日期类（"今天是几月几号" / "今天多少号"）——用代码回答，避免 LLM 瞎编日期
    if (
        "几月几号" in t
        or "今天日期" in t
        or "今天多少号" in t
        or ("几号" in t and any(w in t for w in ("今天", "现在", "当前")))
    ):
        return {"intent": "today_date"}

    # 倒计时类：
    #   A. "还有几天到国庆节" / "距离国庆还有几天"（节日在"还有/距离"之后）
    #   B. "最近的清明节还有几天"（节日在"还有"之前）
    if ("还有" in t or "距离" in t or "倒计时" in t) and ("天" in t or "节" in t):
        target = ""
        m = re.search(r"(?:还有|距离|倒计时)\s*([^，。！？!?]{1,10})", t)
        if m:
            target = re.sub(r"^(几天到|多少天到|到|至|几天|多少天|还有几天|还有多少天|还有)", "", m.group(1)).strip()
            target = re.sub(r"(还有几天|还有多少天|还有)$", "", target).strip()
        if not target:
            pos = -1
            for kw in ("还有", "距离", "倒计时"):
                p = t.find(kw)
                if p > 0 and (pos == -1 or p < pos):
                    pos = p
            if pos > 0:
                before = t[max(0, pos - 10):pos]
                before = re.sub(r"(最近的|下一个|下个|距离|到|过|的|呢|，|,|今年|明年|还有)", "", before).strip()
                cands = re.findall(r"[\u4e00-\u9fa5]{2,4}", before)
                target = cands[-1] if cands else ""
        if target:
            return {"intent": "countdown", "target": target}

    return None


# ---- LLM 层：research / hotlist / chat ----

INTENT_SYSTEM = """你是资讯调研助手的意图解析器。判断用户意图并输出 JSON。

用户输入可能属于三类：
- hotlist：要求热搜榜/热榜/TOP榜单类内容（关键词：热搜、热榜、十大、TOP、排名、榜单、排行、最热）
  如"今天十大热点"、"百度热搜"、"Bing热搜榜"、"微博热搜前十"
- research：要求获取/总结/整理/调研资讯类内容（非榜单）
  如"本周AI新闻"、"这个月OpenAI动态"、"近三天生物领域科研热点"
- chat：问候、自我介绍、道谢等非资讯请求，如"你好"、"你是谁"、"谢谢"

输出 JSON，字段：
{
  "intent": "hotlist" 或 "research" 或 "chat",
  "topic": "要调研的主题（中文，去掉时间词和"热点/新闻/总结"等泛词；hotlist 可为空）",
  "topic_en": "主题的英文关键词（2~5个单词，小写，用于英文搜索源，如 gene editing / artificial intelligence）",
  "source": "榜单平台：bing/必应 或 baidu/百度 或 weibo/微博 或 zhihu/知乎 或 bilibili/B站，可为空（仅 hotlist 需要）",
  "count": "榜单条数（用户说"前100/TOP50/20个"时填对应数字；没提就填10；仅 hotlist 需要）",
  "topic_slug": "3-8个字母的英文标识，用于文件名，如 ai/biology/tech/general/bing/baidu，小写无空格",
  "time_window": "时间范围：今天 / 本周 / 上周 / 本月 / 近N天（N为数字）",
  "title": "简短中文报告标题，不含日期，如：生物领域科研热点周报",
  "subtopics": ["拆分角度1", "拆分角度2", "拆分角度3"]（3~5个，用于分头调研）
}

规则（优先级从高到低）：
- 包含"总结/资讯/盘点/整理/汇总/综述/热点新闻/热点资讯"等词（无论是否含"热榜/热搜"）→ intent=research（资讯总结类）
- 仅包含"热搜榜/热榜/TOP/前N/排名/榜单/排行/最热"等榜单词，且不含上述总结类词 → intent=hotlist
- 用户没提时间范围时，time_window 默认"今天"
- hotlist 类：topic_slug 取平台（bing/baidu/weibo/zhihu/bilibili），title 如"今日十大热点"
- intent=chat 时，其余字段可为空字符串
- 只输出 JSON，不要任何解释"""

SOURCE_SLUG_MAP = {
    "bing": "bing", "必应": "bing",
    "baidu": "baidu", "百度": "baidu",
    "weibo": "weibo", "微博": "weibo",
    "zhihu": "zhihu", "知乎": "zhihu",
    "bili": "bilibili", "b站": "bilibili", "哔哩哔哩": "bilibili",
}

# 平台名 → 热榜源（"XX平台热点资讯"类请求，热榜才是该平台真实热点）
PLATFORM_WORDS = {
    "微博": "weibo", "知乎": "zhihu", "百度": "baidu", "必应": "bing",
    "b站": "bilibili", "哔哩哔哩": "bilibili",
}

_TOPIC_GENERIC = re.compile(
    r"今天|今日|昨天|昨日|前天|本周|上周|本月|上月|近\d+天|热点|资讯|新闻|总结|盘点|汇总|整理|动态|综述|报道|给我|关于|的|领域|方向|内容"
)


def _redirect_platform_to_hotlist(topic: str, source: str, time_window: str) -> dict | None:
    """research 主题若清洗后只剩平台名 → 转 hotlist。

    原因：搜索源（Bing/HN/少数派）拿不到微博/知乎等平台站内数据，
    该平台真实热点就是热榜。如"微博热点资讯总结"→ 微博热榜。
    """
    # 1) LLM 已给出平台 source 字段（如把"微博热榜"分到 research）→ 直接转
    if source:
        for k, v in SOURCE_SLUG_MAP.items():
            if k in source:
                return {
                    "intent": "hotlist", "source": source, "count": 10,
                    "topic_slug": v, "time_window": time_window,
                    "title": f"{source}今日热点",
                }
    # 2) 主题清洗后只剩平台名
    cleaned = _TOPIC_GENERIC.sub("", topic or "").strip()
    for plat, slug in PLATFORM_WORDS.items():
        if plat in cleaned:
            return {
                "intent": "hotlist", "source": plat, "count": 10,
                "topic_slug": slug, "time_window": time_window,
                "title": f"{plat}今日热点",
            }
    return None


# 纯虚词表：去掉后若输入为空，说明用户没给出任何实质主题（需要澄清）
_VOID_WORDS = re.compile(
    r"帮我|给我|帮我查|帮我找|查一下|查查|查|看看|问一下|一下|请问|我想|我要|想要|"
    r"了解|知道|说说|讲讲|介绍|来|那个|这个|的|了|呢|啊|吧|呀|吗"
)

# 省略句模式："西安呢？" / "西安怎么样" / "后天呢" —— subject + 语气词/询问词结尾
_ELLIPSIS_PAT = re.compile(r"^(.{1,6}?)\s*(?:呢|怎么样|如何|咋样|什么情况|啥情况)\s*[？?]?$")
_ELLIPSIS_TIME = ("今天", "明天", "后天", "昨天", "前天")


def resolve_ellipsis(text: str, memory=None) -> tuple[str | None, dict | None]:
    """解析承前省略句（规则层，零 LLM 成本）。

    例：上一轮「哈尔滨明天什么天气」，本轮「西安呢？」→ 应理解为「西安明天什么天气」。

    返回 (补全后的句子, 可直接执行的结构化意图)；两者均为 None 表示不是省略句。
    - subject 是城市且上轮是天气查询 → 直接构造天气意图（复用上轮日期/雨雪参数）
    - subject 是时间词且上轮是周几/日期查询 → 直接构造 weekday 意图
    - 有其他上轮查询 → 补全句子交给 LLM 解析（更稳）
    """
    t = (text or "").strip().strip("？?。！!，, ")
    m = _ELLIPSIS_PAT.match(t)
    if not m:
        return None, None
    subject = m.group(1).strip()
    if not subject:
        return None, None
    if memory is None:
        return None, None
    last = memory.get_fact("last_query_intent")
    if not last:
        return None, None

    # 1) 承前天气：主语是城市 → 复用上轮的日期与雨雪参数
    if last.get("intent") == "weather":
        if subject in CITY_CODES:
            return f"{subject}{last.get('day', '今天')}天气怎么样", {
                "intent": "weather",
                "day": last.get("day", "今天"),
                "city": subject,
                "city_kind": "known",
                "ask_rain": last.get("ask_rain", False),
            }
        if subject in KNOWN_FOREIGN:
            return f"{subject}{last.get('day', '今天')}天气怎么样", {
                "intent": "weather",
                "day": last.get("day", "今天"),
                "city": "未知城市",
                "city_kind": "unknown",
                "ask_rain": last.get("ask_rain", False),
            }
        return None, None  # 不是城市，不硬猜天气

    # 2) 承前周几：主语是时间词 → 复用查询类型
    if last.get("intent") == "weekday" and subject in _ELLIPSIS_TIME:
        return f"{subject}是周几", {"intent": "weekday", "day": subject}

    # 3) 承前其他查询（research/hotlist 等）：补全句子，交给 LLM 解析
    if subject:
        return f"继续上次的{last.get('intent', '')}请求，主题：{subject}", None

    return None, None


def _needs_clarify(user_input: str, data: dict) -> bool:
    """判断是否需要澄清：LLM 没解析出可执行信息，且输入本身信息量不足。

    保守策略：只有"明显缺主题"才追问（避免打扰）；
    有默认值可执行的情况（如 hotlist 无平台 → 综合榜）不追问。
    """
    if not data:
        return True
    intent = data.get("intent")
    if intent in ("chat", "hotlist"):
        return False  # 闲聊无需主题；榜单可走综合源
    if intent == "research":
        # 用户输入去掉虚词后没有实质内容 → 必须澄清（无论 LLM 给了什么主题）
        cleaned = _VOID_WORDS.sub("", user_input).strip()
        if len(cleaned) < 2:
            return True
        topic = (data.get("topic") or "").strip()
        if topic and topic != "全网热点":
            return False
        # 主题为空/默认值：看输入是否含实质名词（"热点/新闻"等泛词也算可执行）
        cleaned2 = _TOPIC_GENERIC.sub("", user_input).strip()
        if len(cleaned2) >= 2:
            return False
        return True
    return False


def parse_intent(user_input: str, memory=None) -> dict:
    """LLM 把用户一句话解析为结构化任务。

    - memory 可选：注入最近对话历史帮助理解指代（如"再讲讲第二条"）
    - 解析失败或信息不足：返回 {"intent": "clarify", "question": ...}，
      由 Agent 核心循环追问，而非静默回退为通用调研（读懂目标的落点）
    """
    user_msg = user_input
    if memory is not None:
        ctx = memory.context_blurb(n=4)
        if ctx:
            user_msg = f"对话历史（仅供理解指代，勿据此编造新事实）：\n{ctx}\n\n当前请求：{user_input}"

    data = call_llm_json(INTENT_SYSTEM, user_msg, temperature=0.2, fallback={})

    if _needs_clarify(user_input, data):
        return {
            "intent": "clarify",
            "question": (
                "我理解您想让我做资讯调研，但还需要明确一点：您想调研什么主题？\n"
                "  例：AI 热点 / 生物科研 / OpenAI 动态 / 全网热点\n"
                "（直接回复主题即可；回「全网热点」我会按综合热点调研）"
            ),
        }

    intent = data.get("intent", "research")

    if intent == "chat":
        return {"intent": "chat"}

    if intent == "hotlist":
        source = (data.get("source") or "").strip()
        src_slug = ""
        for k, v in SOURCE_SLUG_MAP.items():
            if k in (source + (data.get("topic_slug") or "")):
                src_slug = v
                break
        # 数量解析：规则优先（"前100"/"TOP50"/"100个"/"100条"），LLM 的 count 兜底
        count = 10
        m = re.search(r"(?:前|TOP|top|Top)\s*(\d{1,4})", user_input) or re.search(r"(\d{1,4})\s*(?:个|条|大)", user_input)
        if m:
            count = min(int(m.group(1)), 200)
        elif str(data.get("count") or "").isdigit():
            count = min(int(data["count"]), 200)
        return {
            "intent": "hotlist",
            "source": source,
            "count": count,
            "topic_slug": src_slug or "hotlist",
            "time_window": data.get("time_window") or "今天",
            "title": (data.get("title") or "今日十大热点").strip() or "今日十大热点",
        }

    topic = (data.get("topic") or "全网热点").strip() or "全网热点"
    redirect = _redirect_platform_to_hotlist(topic, data.get("source") or "", data.get("time_window") or "今天")
    if redirect:
        return redirect
    return {
        "intent": "research",
        "topic": topic,
        "topic_en": (data.get("topic_en") or "").strip(),
        "topic_slug": re.sub(r"[^a-z]", "", (data.get("topic_slug") or "general").lower())[:8] or "general",
        "time_window": data.get("time_window") or "今天",
        "title": (data.get("title") or "热点资讯汇总").strip() or "热点资讯汇总",
        "subtopics": [s for s in (data.get("subtopics") or []) if isinstance(s, str) and s.strip()][:5],
    }
