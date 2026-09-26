"""日常事实查询：周几 / 节日倒计时 / 城市识别（确定性计算，零 LLM 成本）。"""
import re
from datetime import date, timedelta

from .config import CITY_CODES, DEFAULT_CITY

# 支持倒计时的节日。清明节是节气（每年4月4-5日浮动），按4月4日近似估算。
FESTIVALS = {
    "元旦": (1, 1),
    "情人节": (2, 14),
    "妇女节": (3, 8),
    "植树节": (3, 12),
    "清明节": (4, 4),   # 节气，日期浮动
    "劳动节": (5, 1),
    "青年节": (5, 4),
    "儿童节": (6, 1),
    "建党节": (7, 1),
    "建军节": (8, 1),
    "教师节": (9, 10),
    "国庆节": (10, 1),
    "圣诞": (12, 25),
    "平安夜": (12, 24),
}

# 日期为近似值的节日（节气类），回复时标注"约"
APPROX_FESTIVALS = {"清明节"}

DAY_OFFSET = {"今天": 0, "明天": 1, "后天": 2}

# 常见国外/境外城市：命中但不在内置表 → 明确提示"暂不支持"而不是静默用默认城市
KNOWN_FOREIGN = (
    "东京", "大阪", "京都", "纽约", "洛杉矶", "旧金山", "伦敦", "巴黎", "柏林",
    "罗马", "悉尼", "墨尔本", "首尔", "新加坡", "曼谷", "迪拜", "莫斯科",
    "多伦多", "温哥华",
)


def _city_candidate(text: str) -> str | None:
    """从"XX天气/XX下雨"结构里提取城市候选（2~4个汉字，非时间词）。

    例："哈尔滨明天什么天气" → "哈尔滨"；"明天天气" → None。
    """
    for kw in ("天气", "下雨", "下雪", "降雨"):
        idx = text.find(kw)
        if idx > 0:
            before = text[max(0, idx - 10):idx]
            before = re.sub(
                r"(今天|明天|后天|昨天|前天|明天会|今天会|后天会|会|的|什么|怎么样|如何|"
                r"是否|有没有|有|没|下不下|还|呢|想|看看|问|看)",
                "", before,
            ).strip()
            cands = re.findall(r"[\u4e00-\u9fa5]{2,4}", before)
            return cands[-1] if cands else None
    return None


def extract_city(text: str) -> tuple[str, str]:
    """从问句里提取城市。返回 (城市名, 类型)。

    类型：
        known  —— 命中内置城市表
        unknown —— 提了城市（含常见国外城市与"XX天气"结构）但不在表里
        absent —— 未提任何城市（用默认城市）
    """
    for name in CITY_CODES:
        if name in text:
            return name, "known"
    for name in KNOWN_FOREIGN:
        if name in text:
            return "未知城市", "unknown"
    if _city_candidate(text):
        return "未知城市", "unknown"
    if re.search(r"[\u4e00-\u9fa5]{1,4}(?:市|城)", text):
        return "未知城市", "unknown"
    return DEFAULT_CITY, "absent"


def answer_weekday(day: str = "明天") -> str:
    """"明天是周几"类问题的回答。"""
    offset = DAY_OFFSET.get(day, 1)
    d = date.today() + timedelta(days=offset)
    wd = "一二三四五六日"[d.weekday()]
    return f"{day}是{d.month}月{d.day}日，星期{wd}。"


def answer_today_date() -> str:
    """"今天是几月几号"类问题的回答。"""
    d = date.today()
    wd = "一二三四五六日"[d.weekday()]
    return f"今天是{d.year}年{d.month}月{d.day}日，星期{wd}。"


def answer_countdown(target: str) -> str:
    """"还有几天到国庆节"类问题的回答。"""
    # 目标可能带"节"字后缀（如"国庆节"），先去掉再匹配
    name = re.sub(r"节$", "", target)
    # 精确匹配节日名
    hit = None
    for k in FESTIVALS:
        if k in target or k == name or name in k:
            hit = k
            break
    if not hit:
        # 尝试"国庆"等简称
        for k in FESTIVALS:
            if name and (k.startswith(name) or name.startswith(k)):
                hit = k
                break
    if not hit:
        return f"我不太确定「{target}」的日期，可以换一个我认识的节日，比如国庆节、元旦、劳动节。"

    month, day_ = FESTIVALS[hit]
    today = date.today()
    target_date = date(today.year, month, day_)
    if target_date < today:
        target_date = date(today.year + 1, month, day_)  # 今年已过，算明年的
    delta = (target_date - today).days
    if delta == 0:
        return f"今天就是{hit}！节日快乐！"
    approx = "约" if hit in APPROX_FESTIVALS else ""
    approx_note = f"（{hit}每年日期在{month}月{day_}日前后浮动，此为估算）" if hit in APPROX_FESTIVALS else ""
    return f"距离{hit}还有 {delta} 天（{approx}{target_date.month}月{target_date.day}日）{approx_note}"
