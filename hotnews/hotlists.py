"""热榜抓取与榜单报告（hotlist）。

可用源（均为实时快照，无历史数据）：
    - 百度热搜实时榜（top.baidu.com）：通用热点，单页最多约 50 条
    - B站热搜词（api.bilibili.com search/square）：搜索热词，约 10 条
    - B站热门视频（api.bilibili.com popular）：官方热门，单次最多 50 条
    - B站全站排行榜（api.bilibili.com ranking/v2）：日榜，一次返回 100 条
不可用（需登录态）：微博热搜（403）、知乎热榜（401）

关于"昨天B站热榜"这类历史榜单请求：热榜是实时快照，无法回溯。
处理方式：说明原因 → 给出当前实时榜单替代 → 建议可用资讯调研获取昨天热点。
"""
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .config import REQUEST_TIMEOUT
from .time_utils import date_range_of, normalize_window, time_expr

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

BAIDU_MAX = 50    # 百度热搜实时榜单页实际上限
BILI_MAX = 50     # B站热门视频 ps 参数上限
RANK_MAX = 100    # B站全站排行榜单次返回上限


def fetch_baidu_hot(n: int = 10) -> list[dict]:
    """百度热搜实时榜 TOP n（最多 BAIDU_MAX 条）。返回 [{rank, title}]。失败返回空列表。"""
    n = min(n, BAIDU_MAX)
    try:
        resp = requests.get(
            "https://top.baidu.com/board?tab=realtime",
            headers={"User-Agent": UA},
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        titles = soup.select("div.c-single-text-ellipsis")
        items = []
        for i, t in enumerate(titles[:n], 1):
            text = t.get_text(" ", strip=True)
            if text:
                items.append({"rank": i, "title": text})
        return items
    except Exception:
        return []


def fetch_bilibili_hot_words(n: int = 10) -> list[dict]:
    """B站热搜词 TOP n（search/square 接口，data.trending.list）。失败返回空列表。"""
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/search/square",
            params={"limit": n},
            headers={"User-Agent": UA},
            timeout=20,
        )
        resp.raise_for_status()
        j = resp.json()
        words = j.get("data", {}).get("trending", {}).get("list", [])[:n]
        return [
            {"rank": i + 1, "title": w.get("keyword", ""), "extra": ""}
            for i, w in enumerate(words) if w.get("keyword")
        ]
    except Exception:
        return []


def fetch_bilibili_popular(n: int = 10) -> list[dict]:
    """B站热门视频 TOP n（最多 BILI_MAX 条）。返回 [{rank, title, extra}]。失败返回空列表。"""
    n = min(n, BILI_MAX)
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/popular",
            params={"ps": n},
            headers={"User-Agent": UA},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        vlist = data.get("data", {}).get("list", [])[:n]
        return [
            {
                "rank": i + 1,
                "title": v.get("title", ""),
                "extra": f"播放 {v.get('stat', {}).get('view', 0):,}",
            }
            for i, v in enumerate(vlist) if v.get("title")
        ]
    except Exception:
        return []


def fetch_bilibili_ranking(n: int = 10) -> list[dict]:
    """B站全站排行榜 TOP n（ranking/v2，一次返回最多 100 条）。失败返回空列表。"""
    n = min(n, RANK_MAX)
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/ranking/v2",
            params={"rid": 0, "type": "all"},
            headers={"User-Agent": UA},
            timeout=20,
        )
        resp.raise_for_status()
        j = resp.json()
        vlist = j.get("data", {}).get("list", [])[:n]
        return [
            {
                "rank": i + 1,
                "title": v.get("title", ""),
                "extra": f"播放 {v.get('stat', {}).get('view', 0):,}",
            }
            for i, v in enumerate(vlist) if v.get("title")
        ]
    except Exception:
        return []


def run_hotlist(intent: dict) -> str:
    """榜单类请求：直接抓热榜数据生成 TOP 列表。快、准、不调 LLM 汇总。

    - count：用户要的条数（默认 10），各源按自身上限截取并在报告注明
    - 历史窗口（昨天/前天等）：热榜无历史 → 说明原因 + 给实时榜替代 + 给建议
    - source 指定平台；无指定或未知源时抓全部可用源（综合榜）
    """
    source = intent.get("source", "")
    count = intent.get("count", 10)
    window_kind, time_slug = normalize_window(intent.get("time_window", "今天"))
    dr = date_range_of(window_kind)
    slug = intent.get("topic_slug", "hotlist")
    title = intent.get("title", "今日十大热点")

    # 历史窗口说明：热榜是实时快照，无历史数据
    history_note = ""
    if window_kind not in ("今天",):
        history_note = (
            f"（注：您要的是「{window_kind}」的榜单，但热榜是实时快照、没有历史数据，"
            "以下为当前实时榜单。若需要昨天的热点资讯，可对我说\"帮我整理昨天的热点新闻\"，"
            "我会用资讯调研的方式生成报告。）"
        )
        print("[Agent] 说明：热搜榜是实时快照，没有历史数据，无法回溯过去。已为您抓取当前实时榜单；如需昨天的热点资讯，我可以做资讯调研。")

    note_lines = []
    sources = []  # [(区段标题, 抓取函数, 是否可截断)]
    if "bing" in source or "必应" in source:
        note_lines.append("（注：Bing 无公开热搜榜数据，已使用百度热搜实时榜替代）")
        sources.append(("百度热搜", fetch_baidu_hot))
    elif "bili" in source or "b站" in source:
        # B站热榜含两类：热搜（搜索热词）与热播（热门视频/全站排行），都给出
        sources.append(("B站热搜词", fetch_bilibili_hot_words))
        sources.append(("B站热门视频", fetch_bilibili_popular))
        sources.append(("B站全站排行榜", fetch_bilibili_ranking))
    elif "weibo" in source or "微博" in source:
        note_lines.append("（注：微博热搜需要登录态，当前无法抓取，已使用百度热搜实时榜替代。如需接入微博热搜，可提供登录 cookie 后由我添加数据源。）")
        sources.append(("百度热搜", fetch_baidu_hot))
    elif "baidu" in source or "百度" in source:
        sources.append(("百度热搜", fetch_baidu_hot))
    else:
        sources = [
            ("百度热搜", fetch_baidu_hot),
            ("B站热搜词", fetch_bilibili_hot_words),
            ("B站热门视频", fetch_bilibili_popular),
            ("B站全站排行榜", fetch_bilibili_ranking),
        ]

    sections = []
    for name, fn in sources:
        if name == "B站热搜词":
            items = fn(10)  # 热搜词固定取 10 条
        else:
            items = fn(count)
        if items:
            sections.append((name, items, len(items) < count))
        elif "B站" in name:
            note_lines.append(f"（{name}接口暂未获取到数据）")

    out_path = f"{time_slug}_{slug}_{dr[0]:%Y-%m-%d}.md"
    if not sections:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# {title}（{datetime.now():%Y-%m-%d}）\n\n## 榜单抓取失败\n\n未能从任何热榜数据源获取数据，请稍后重试。\n")
        print("[Agent] 抱歉，所有热榜数据源都暂时不可用，请稍后再试。")
        return out_path

    lines = [
        f"# {title}",
        f"获取时间：{datetime.now():%Y-%m-%d %H:%M} · 请求时间范围：{time_expr(dr)}",
    ]
    if history_note:
        lines.append(history_note)
    lines.extend(note_lines)
    for name, items, truncated in sections:
        lines.append("")
        lines.append(f"## {name} TOP{len(items)}")
        if truncated:
            lines.append(f"（注：您要 {count} 条，该源单次最多返回 {len(items)} 条，已全部给出）")
        lines.append("")
        lines.append("| # | 内容 |")
        lines.append("|---|------|")
        for it in items:
            extra = f"（{it.get('extra', '')}）" if it.get("extra") else ""
            lines.append(f"| {it['rank']} | {it['title']}{extra} |")
    lines.append("")
    lines.append("> 榜单数据直接抓取自各平台公开接口，按热度排序。")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path
