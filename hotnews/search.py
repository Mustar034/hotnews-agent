"""多源搜索：Bing 通用 → 少数派 RSS 降级；HN 热帖 / HN 关键词 / 少数派 作补充源。"""
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .config import REQUEST_TIMEOUT, SEARCH_LIMIT

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def search(query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """多源容错主入口：Bing 通用 → 少数派 RSS，按顺序降级。

    每个源失败自动尝试下一个；全部失败返回空列表（由 Worker 层兜底）。
    返回结构统一为 [{"title", "url", "snippet"}]。
    """
    sources = (
        lambda q: search_bing(q),
        lambda q: fetch_sspai(),
    )
    for source in sources:
        try:
            results = source(query)[:limit]
            if results:
                return results
        except Exception:
            continue
    return []


def search_bing(query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Bing 通用搜索（cn.bing.com，大陆可访问）。"""
    resp = requests.get(
        "https://cn.bing.com/search",
        params={"q": query, "setlang": "zh-hans", "mkt": "zh-CN"},
        headers={"User-Agent": UA},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select("li.b_algo")[:limit]:
        a = item.select_one("h2 a")
        if not a:
            continue
        p = item.select_one(".b_caption p") or item.select_one("p")
        results.append({
            "title": a.get_text(strip=True),
            "url": a.get("href", ""),
            "snippet": p.get_text(strip=True) if p else "",
        })
    return results


def fetch_sspai(limit: int = 8) -> list[dict]:
    """少数派最新文章 RSS（标准 RSS，中文科技源，可访问性稳定）。失败返回空列表。"""
    try:
        resp = requests.get("https://sspai.com/feed", headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "xml")
        results = []
        for item in soup.select("item")[:limit]:
            title = item.find("title")
            link = item.find("link")
            desc = item.find("description")
            results.append({
                "title": title.get_text(strip=True) if title else "",
                "url": link.get_text(strip=True) if link else "",
                "snippet": _rss_text(desc) if desc else "",
            })
        return [r for r in results if r["title"]]
    except Exception:
        return []


def fetch_hackernews(hours: int = 24, limit: int = 8) -> list[dict]:
    """Hacker News 近 N 小时热帖（免费结构化接口，科技向，增强用）。失败返回空列表。"""
    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "tags": "story",
                "numericFilters": f"created_at_i>{int(time.time()) - hours * 3600}",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])[:limit]
        return [
            {
                "title": h.get("title", ""),
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "snippet": f"HN 评分 {h.get('points', 0)} · 评论 {h.get('num_comments', 0)}",
            }
            for h in hits if h.get("title")
        ]
    except Exception:
        return []


def fetch_hackernews_query(query_en: str, days: int = 7, limit: int = 6) -> list[dict]:
    """Hacker News 按关键词搜索（英文，带时间过滤）。科研/技术类主题的补充源。

    返回 [{title, url, snippet}]，snippet 含 HN 评分与日期。失败返回空列表。
    """
    if not query_en:
        return []
    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query_en,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(time.time()) - days * 86400}",
                "hitsPerPage": limit,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])[:limit]
        return [
            {
                "title": h.get("title", ""),
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "snippet": (
                    f"HN 评分 {h.get('points', 0)} · 评论 {h.get('num_comments', 0)} · "
                    f"{datetime.fromtimestamp(h.get('created_at_i', 0)):%m月%d日}"
                ),
            }
            for h in hits if h.get("title")
        ]
    except Exception:
        return []


def _rss_text(tag) -> str:
    """把 RSS description 里的 HTML 标签剥掉，截取前 200 字。"""
    try:
        text = BeautifulSoup(tag.get_text("", strip=True), "html.parser").get_text(" ", strip=True)
        return text[:200]
    except Exception:
        return ""
