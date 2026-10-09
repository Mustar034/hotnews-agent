"""速递生成：把榜单/调研结果压缩成对话内的"速递"摘要。

格式（参考科技媒体速递风格）：
    科技新闻速递 · 09-30（周三）
    1 一句话要点。一句点评（值得看：...）。来源，日期
    ...
    （最后一行）agent 自己的总结：点出共同主线

速递是对话内的轻量摘要，完整详情由报告（md 文件）承载。
"""
from datetime import datetime
from urllib.parse import urlparse

from .llm import call_llm

DIGEST_MAX = 8  # 速递最多展示条数


def _header(topic: str) -> str:
    now = datetime.now()
    weekdays = "一二三四五六日"
    return f"{topic}速递 · {now:%m-%d}（周{weekdays[now.weekday()]}）"


def _domain(url: str) -> str:
    """从 URL 提取注册域名（去掉 www.），作为速递的来源标注。"""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


def _summarize(titles: list[str]) -> str:
    """基于榜单标题/要点，让 LLM 写一句观察性总结；失败返回空串。"""
    if not titles:
        return ""
    try:
        return call_llm(
            "你是资讯编辑。基于下面这些热搜标题，用一句话写出观察性总结"
            "（不超过50字，点出主题分布或今日焦点；只做归纳，不编造事实、不涉及具体内容评价）：",
            "\n".join(titles),
            temperature=0.6,
        ).strip()
    except Exception:
        return ""


def make_research_digest(results: list[dict], scenario: dict) -> str:
    """调研结果 → 速递文本。

    results 为 research_worker 的结构化输出；scenario 提供主题名。
    速递条目：一句话要点 + 一句点评 +（来源域名，日期）；最后一句为编辑总结。
    兜底：LLM 标了「（背景，非本期）」的要点一律不上速递。
    单日窗口放宽场景（_single_day_note=relaxed）：速递开头如实说明
    「今日发布较少，含最近N天内容」，日期逐条标注，不冒充当天。
    """
    topic = scenario.get("topic", "热点")
    blocks = []
    for r in results:
        domains = ", ".join(_domain(u) for u in r.get("sources", [])[:2])
        for kp in r.get("key_points", [])[:3]:
            if "背景，非本期" in kp or "非本期" in kp:
                continue  # 背景条目不上速递
            blocks.append(f"- {kp}（来源：{domains or '见报告'}）")
    if not blocks:
        return f"{_header(topic)}\n本次调研未获取到可展示的速递条目。\n"

    relaxed = any(r.get("_single_day_note") == "relaxed" for r in results)  # 兼容旧标记，不再输出提示句
    sys_p = (
        "你是资深科技媒体编辑，把下面的调研要点整理成『速递』风格。要求：\n"
        "1. 逐条编号输出（4~8条），每条格式：\n"
        "   序号 一句话要点（≤60字）。一句点评（≤25字，用『值得看：』『信号：』『注意：』『背景：』等开头）。来源域名，日期\n"
        "2. 每条只使用我提供的要点，不得编造；日期从要点中提取；"
        "没有明确日期时省略日期部分（格式为『来源域名』即可，不要写『日期不详』）；"
        "若某条要点的日期不属于本期时间范围，不要输出该条；"
        "优先保留新模型/技术突破/产品发布/开源/硬件/开发者工具类新闻，"
        "剔除纯政治表态、社会议题等非技术新闻\n"
        "3. 来源必须使用括号里提供的真实域名\n"
        "4. 去重：内容相同或高度相似的要点（同一新闻/同一发布）只保留一条，"
        "优先保留信息最完整、带明确日期的那条，绝不允许重复输出\n"
        "5. 最后单独一行输出总结句：用『这N条其实在同一条线上：』或类似句式点出共同主线，"
        "这是编辑的观察性评论，不编造事实，不超过60字\n"
        "6. 只输出速递正文（序号开头），不要标题、不要解释"
    )
    try:
        raw = call_llm(sys_p, "\n".join(blocks[:12]), temperature=0.6).strip()
    except Exception:
        raw = ""
    if raw:
        return f"{_header(topic)}\n{raw}\n"
    # LLM 失败兜底：直接列要点（去掉"日期不详"标注）
    lines = [f"{i + 1} {b[2:].replace('（日期不详）', '').replace('日期不详', '')}"
             for i, b in enumerate(blocks[:DIGEST_MAX])]
    return f"{_header(topic)}\n" + "\n".join(lines) + "\n"


def make_hotlist_digest(data: dict, topic: str = "") -> str:
    """榜单数据 → 速递文本。data 为 fetch_hotlist 的返回；topic 覆盖速递标题（如"微博热榜"）。

    单源榜单直接编号；多源榜单每条标注来源区段。
    """
    title = topic or data.get("title", "今日热点")
    sections = data.get("sections", [])
    multi = len(sections) > 1
    rows = []
    for name, it_list, _ in sections:
        for it in it_list:
            extra = it.get("extra", "")
            if multi:
                tag = f"（{name}" + (f"·{extra}" if extra else "") + "）"
            else:
                tag = f"（{extra}）" if extra else ""
            rows.append(f"{len(rows) + 1} {it['title']}{tag}")
    if not rows:
        return f"{_header(title)}\n本次未获取到可展示的榜单条目。\n"

    shown = rows[:DIGEST_MAX]
    summary = _summarize([r.split(" ", 1)[1] for r in shown])
    lines = [f"{_header(title)}", *shown]
    if len(rows) > DIGEST_MAX:
        lines.append(f"（仅展示前{DIGEST_MAX}条，完整榜单可生成报告查看）")
    if summary:
        lines.append(summary)
    return "\n".join(lines) + "\n"
