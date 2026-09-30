"""热榜抓取与榜单报告（hotlist）。

可用源（均为实时快照，无历史数据）：
    - 百度热搜实时榜（top.baidu.com）：通用热点，单页最多约 50 条
    - B站热搜词（api.bilibili.com search/square）：搜索热词，约 10 条
    - B站热门视频（api.bilibili.com popular）：官方热门，单次最多 50 条
    - B站全站排行榜（api.bilibili.com ranking/v2）：日榜，一次返回 100 条
    - 微博热搜（weibo.com/ajax/side/hotSearch）：需登录 Cookie（.env 的 WEIBO_COOKIE），约 50 条
    - 知乎热榜（zhihu.com/api/v3/feed/topstory/hot-lists）：需登录 Cookie（.env 的 ZHIHU_COOKIE），约 50 条

关于"昨天B站热榜"这类历史榜单请求：热榜是实时快照，无法回溯。
处理方式：说明原因 → 给出当前实时榜单替代 → 建议可用资讯调研获取昨天热点。
"""
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .config import REQUEST_TIMEOUT, WEIBO_COOKIE, ZHIHU_COOKIE
from .time_utils import date_range_of, normalize_window, time_expr

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

BAIDU_MAX = 50    # 百度热搜实时榜单页实际上限
BILI_MAX = 50     # B站热门视频 ps 参数上限
RANK_MAX = 100    # B站全站排行榜单次返回上限
WEIBO_MAX = 50    # 微博热搜实时榜实际上限
ZHIHU_MAX = 50    # 知乎热榜单次返回上限


def _fmt_num(n) -> str:
    """把微博热度整数格式化为 1.2亿 / 345.6万 / 1234。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(int(n))


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


def fetch_weibo_hot(n: int = 10) -> list[dict]:
    """微博热搜 TOP n（weibo.com/ajax/side/hotSearch，需 .env 的 WEIBO_COOKIE）。

    返回 [{rank, title, extra}]；Cookie 失效/风控/接口异常时返回 [] 并给出提示。
    """
    if not WEIBO_COOKIE:
        return []
    n = min(n, WEIBO_MAX)
    try:
        resp = requests.get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={"User-Agent": UA, "Cookie": WEIBO_COOKIE, "Referer": "https://weibo.com/"},
            timeout=20,
        )
        if resp.status_code in (401, 403):
            print("[Agent] 微博 Cookie 已失效或账号被风控，请重新登录微博，更新 .env 的 WEIBO_COOKIE。")
            return []
        resp.raise_for_status()
        j = resp.json()
        if j.get("ok") != 1:
            print("[Agent] 微博热搜接口返回异常（Cookie 可能失效），请更新 .env 的 WEIBO_COOKIE。")
            return []
        realtime = j.get("data", {}).get("realtime", [])[:n]
        items = []
        for i, it in enumerate(realtime, 1):
            word = it.get("word", "")
            if not word:
                continue
            parts = [p for p in (it.get("label_name", ""), _fmt_num(it.get("num"))) if p]
            items.append({"rank": i, "title": word, "extra": " ".join(parts)})
        return items
    except Exception:
        return []


def fetch_zhihu_hot(n: int = 10) -> list[dict]:
    """知乎热榜 TOP n（zhihu.com/api/v3/feed/topstory/hot-lists，需 .env 的 ZHIHU_COOKIE）。

    注意：知乎接口有 x-zse-96 签名风控，纯 requests 访问不稳定
    （常报 ERR_TICKET_NOT_EXIST / 安全验证页）。新 Cookie 首次请求可能放行，
    后续大概率被拦截。返回 [{rank, title, extra}]；失败返回 [] 并给出提示。
    """
    if not ZHIHU_COOKIE:
        return []
    n = min(n, ZHIHU_MAX)
    try:
        resp = requests.get(
            "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total",
            params={"limit": n, "desktop": "true"},
            headers={"User-Agent": UA, "Cookie": ZHIHU_COOKIE, "Referer": "https://www.zhihu.com/"},
            timeout=20,
        )
        if resp.status_code in (401, 403):
            print("[Agent] 知乎接口要求签名验证或账号被风控（纯脚本访问不稳定）。可尝试：1) 重新登录知乎获取新 Cookie 再试；2) 换浏览器自动化方案；3) 暂时放弃知乎热榜。")
            return []
        resp.raise_for_status()
        j = resp.json()
        data = j.get("data", [])[:n]
        items = []
        for i, it in enumerate(data, 1):
            target = it.get("target", {})
            title = target.get("title", "")
            if not title:
                continue
            detail = it.get("detail_text", "") or target.get("detail_text", "")
            items.append({"rank": i, "title": title, "extra": detail})
        return items
    except Exception:
        return []


def fetch_hotlist(intent: dict) -> dict:
    """榜单类请求：抓取热榜数据，返回结构化结果（不写文件，由调用方决定是否落盘）。

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
    sources = []  # [(区段标题, 抓取函数)]
    if "bing" in source or "必应" in source:
        note_lines.append("（注：Bing 无公开热搜榜数据，已使用百度热搜实时榜替代）")
        sources.append(("百度热搜", fetch_baidu_hot))
    elif "bili" in source or "b站" in source:
        # B站热榜含两类：热搜（搜索热词）与热播（热门视频/全站排行），都给出
        sources.append(("B站热搜词", fetch_bilibili_hot_words))
        sources.append(("B站热门视频", fetch_bilibili_popular))
        sources.append(("B站全站排行榜", fetch_bilibili_ranking))
    elif "weibo" in source or "微博" in source:
        if not WEIBO_COOKIE:
            note_lines.append(
                "（注：未配置微博 Cookie，无法抓取微博热搜。请在项目根目录 .env 中设置 WEIBO_COOKIE=你的微博Cookie 后重试，或对我说\"微博热榜\"获取配置指引。）"
            )
            print("[Agent] 说明：抓取微博热搜需要登录 Cookie，当前 .env 未配置 WEIBO_COOKIE。请登录微博后按 README 指引配置。")
        else:
            sources.append(("微博热搜", fetch_weibo_hot))
    elif "zhihu" in source or "知乎" in source:
        if not ZHIHU_COOKIE:
            note_lines.append(
                "（注：未配置知乎 Cookie，无法抓取知乎热榜。请在项目根目录 .env 中设置 ZHIHU_COOKIE=你的知乎Cookie 后重试，或对我说\"知乎热榜\"获取配置指引。）"
            )
            print("[Agent] 说明：抓取知乎热榜需要登录 Cookie，当前 .env 未配置 ZHIHU_COOKIE。请登录知乎后按 README 指引配置。")
        else:
            sources.append(("知乎热榜", fetch_zhihu_hot))
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

    return {
        "title": title,
        "sections": sections,
        "note_lines": note_lines,
        "history_note": history_note,
        "count": count,
        "file_name": f"{time_slug}_{slug}_{dr[0]:%Y-%m-%d}.md",
        "time_expr_str": time_expr(dr),
        "date_str": f"{datetime.now():%Y-%m-%d}",
    }


def write_hotlist(data: dict) -> str:
    """把 fetch_hotlist 的结果写成 md 报告，返回文件路径。"""
    out_path = data["file_name"]
    title = data["title"]
    sections = data["sections"]
    note_lines = data["note_lines"]
    history_note = data["history_note"]
    count = data["count"]

    if not sections:
        with open(out_path, "w", encoding="utf-8") as f:
            fail_lines = [f"# {title}（{data['date_str']}）", "", "## 榜单抓取失败", "未能从任何热榜数据源获取数据。"]
            fail_lines.extend(note_lines)
            f.write("\n".join(fail_lines) + "\n")
        if note_lines:
            print(f"[Agent] {note_lines[0]}")
        else:
            print("[Agent] 抱歉，所有热榜数据源都暂时不可用，请稍后再试。")
        return out_path

    lines = [
        f"# {title}",
        f"获取时间：{datetime.now():%Y-%m-%d %H:%M} · 请求时间范围：{data['time_expr_str']}",
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
    print("[完成] 榜单已保存：" + out_path)
    return out_path
