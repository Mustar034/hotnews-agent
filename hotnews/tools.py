"""工具注册表：把 Agent 可调用的能力统一登记元数据，并提供统一调用入口。

调用工具的落点：
- 每个工具带元数据（名称/说明/来源域名/是否消耗 LLM/成本级别），可审计、可解释；
- 所有工具调用经 run_tool 统一收口：记录执行轨迹（trace）、统计调用次数；
- 确定性工具（天气/日期/榜单）零 LLM 成本，与智能工具在注册表里明确区分；
- 失败返回统一结构（空列表/None），由上层显式降级，绝不静默中断。

注意：本注册表是"可解释/可控"的台账，不是智能路由。当前架构仍是
"规则优先 + 意图类型分派"（确定性逻辑用代码，需要理解才调 LLM），
注册表不改变这一成本可控的设计。
"""
from . import facts, hotlists, weather
from .trace import get_trace

# 工具元数据：(说明, 来源域名, 成本级别)
# 成本级别：code=确定性零 LLM / llm=智能调用 / net=网络抓取（零 LLM）
TOOL_REGISTRY = {
    # ---- 搜索与资讯 ----
    "search_bing":             ("Bing 通用搜索", "cn.bing.com", "net"),
    "fetch_sspai":             ("少数派最新文章 RSS", "sspai.com", "net"),
    "fetch_hackernews":        ("Hacker News 近 N 小时热帖", "hn.algolia.com", "net"),
    "fetch_hackernews_query":  ("Hacker News 关键词搜索（带时间过滤）", "hn.algolia.com", "net"),
    # ---- 热榜（零 LLM）----
    "fetch_baidu_hot":         ("百度热搜实时榜 TOP n", "top.baidu.com", "code"),
    "fetch_bilibili_hot_words":("B站热搜词", "api.bilibili.com", "code"),
    "fetch_bilibili_popular":  ("B站热门视频", "api.bilibili.com", "code"),
    "fetch_bilibili_ranking":  ("B站全站排行榜", "api.bilibili.com", "code"),
    "fetch_weibo_hot":         ("微博热搜（需 Cookie）", "weibo.com", "code"),
    "fetch_zhihu_hot":         ("知乎热榜（需 Cookie）", "zhihu.com", "code"),
    # ---- 天气（零 LLM）----
    "fetch_weather":           ("中国天气网 7 天预报", "weather.com.cn", "code"),
    # ---- 事实计算（零 LLM、确定性）----
    "answer_weekday":          ("周几计算", "code", "code"),
    "answer_today_date":       ("当前日期", "code", "code"),
    "answer_countdown":        ("节日倒计时", "code", "code"),
}


def describe_tool(name: str) -> str:
    """返回工具的说明文本（供可解释输出/计划展示）。"""
    meta = TOOL_REGISTRY.get(name)
    if not meta:
        return name
    desc, source, level = meta
    cost = {"code": "零成本", "net": "网络抓取", "llm": "消耗LLM"}[level]
    return f"{desc}（{source}，{cost}）"


def run_tool(name: str, **kwargs):
    """统一工具调用入口：查注册表 → 记轨迹 → 执行 → 记录结果状态。

    返回与底层函数一致的返回结构（列表/dict/str）；底层函数已内置
    try/except 降级（失败返回空），此处只负责记录，不重复降级。
    """
    if name not in TOOL_REGISTRY:
        get_trace().log("tool", f"未知工具 {name}", status="error")
        return None

    # 映射：注册表名字 → 真实函数
    fn = {
        "fetch_baidu_hot": hotlists.fetch_baidu_hot,
        "fetch_bilibili_hot_words": hotlists.fetch_bilibili_hot_words,
        "fetch_bilibili_popular": hotlists.fetch_bilibili_popular,
        "fetch_bilibili_ranking": hotlists.fetch_bilibili_ranking,
        "fetch_weibo_hot": hotlists.fetch_weibo_hot,
        "fetch_zhihu_hot": hotlists.fetch_zhihu_hot,
        "fetch_weather": weather.fetch_weather,
        "answer_weekday": facts.answer_weekday,
        "answer_today_date": facts.answer_today_date,
        "answer_countdown": facts.answer_countdown,
    }.get(name)

    get_trace().log("tool", f"调用 {describe_tool(name)}", detail=_fmt_args(kwargs), tool=name)
    if fn is None:
        get_trace().log("tool", f"{name} 需在业务层调用", status="note", tool=name)
        return None

    try:
        result = fn(**kwargs)
        n = len(result) if isinstance(result, (list, dict)) else 0
        get_trace().log("tool", f"{name} 返回", n=n, tool=name, status="ok")
        return result
    except Exception as e:
        get_trace().log("tool", f"{name} 失败：{e}", tool=name, status="error")
        return None


def _fmt_args(kwargs: dict) -> str:
    """把参数压成一行可读摘要（避免展示超长内容）。"""
    parts = []
    for k, v in kwargs.items():
        s = str(v)
        parts.append(f"{k}={s[:40]}{'…' if len(s) > 40 else ''}")
    return "，".join(parts)
