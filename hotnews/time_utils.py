"""时间窗口计算：把"今天/本周/上周/本月/近N天"转成日期范围与搜索词。"""
import re
from datetime import datetime, timedelta


def normalize_window(text: str) -> tuple[str, str]:
    """把意图里的时间窗口转成 (窗口名, 文件slug)。"""
    text = text.strip()
    if "前天" in text:
        return "前天", "day_before"
    if "昨天" in text or "昨日" in text:
        return "昨天", "yesterday"
    if "上周" in text:
        return "上周", "last_week"
    if "本周" in text or "这周" in text:
        return "本周", "weekly"
    if "本月" in text or "这个月" in text:
        return "本月", "monthly"
    m = re.search(r"近\s*(\d+)\s*天", text)
    if m:
        return f"近{m.group(1)}天", f"last{m.group(1)}d"
    return "今天", "daily"


def date_range_of(window_kind: str) -> tuple[datetime, datetime]:
    """按窗口名返回 (起始日, 结束日)。"""
    today = datetime.now()
    if window_kind == "昨天":
        d = today - timedelta(days=1)
        return d, d
    if window_kind == "前天":
        d = today - timedelta(days=2)
        return d, d
    if window_kind == "本周":
        start = today - timedelta(days=today.weekday())
        return start, today
    if window_kind == "上周":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if window_kind == "本月":
        return today.replace(day=1), today
    m = re.search(r"(\d+)", window_kind)  # 近N天
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n - 1), today
    return today, today


def time_expr(dr: tuple[datetime, datetime]) -> str:
    """把日期范围转成可搜索的时间表达（日期放前面利于搜索引擎理解）。"""
    start, end = dr
    if start == end:
        return f"{start:%Y年%m月%d日}"
    if start.day == 1 and start.month == end.month and start.year == end.year:
        return f"{start:%Y年%m月}"
    return f"{start:%m月%d日}至{end:%m月%d日}"


def clean_time_words(text: str) -> str:
    """去掉文本里的时间词和日期短语，避免与 time_expr 重复造成搜索歧义。"""
    text = re.sub(r"[在至]?\d{1,2}月\d{1,2}日(?:至\d{1,2}月\d{1,2}日|至\d{1,2}日)?[期间前后]*", "", text)
    text = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "", text)
    for w in ("今天", "今日", "本周", "上周", "这个星期", "本月", "这个月", "近三天", "近七天"):
        text = text.replace(w, "")
    return re.sub(r"\s+", " ", text).strip()


def point_date(kp: str) -> str | None:
    """从要点文本提取日期，返回 'YYYY-MM-DD'；提取不到返回 None。

    支持：2026年10月5日 / 10月5日 / 10-05 / 10/5（月日默认当年）。
    """
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", kp)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日(?!\d)", kp)
    if m:
        y = datetime.now().year
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)", kp)
    if m and int(m.group(1)) <= 12:  # 形如 10-05 的日期，避免误伤
        y = datetime.now().year
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None
