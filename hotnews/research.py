"""调研引擎：拆解 → 并行 Worker → 汇总 → 写报告。

流程：build_scenario 组装场景 → decompose 拆子问题 → ThreadPoolExecutor
并行跑 research_worker（每 Worker 三层搜索 + LLM 提炼）→ synthesize 汇总。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import MAX_WORKERS, SEARCH_LIMIT
from .llm import call_llm, parse_json_llm
from .search import fetch_hackernews, fetch_hackernews_query, fetch_sspai, search
from .time_utils import clean_time_words, date_range_of, normalize_window, time_expr


def build_scenario(intent: dict) -> dict:
    """把意图解析结果组装成引擎可执行的场景配置。"""
    window_kind, time_slug = normalize_window(intent.get("time_window", "今天"))
    dr = date_range_of(window_kind)
    topic = intent.get("topic", "全网热点")
    title = intent.get("title", f"{topic}资讯汇总")
    subtopics = intent.get("subtopics") or [
        "最值得关注的热点事件",
        "重要的行业动态与数据",
        "前沿进展或争议话题",
    ]
    span_days = (dr[1] - dr[0]).days
    return {
        "title": title,
        "topic": topic,
        "topic_en": intent.get("topic_en", ""),
        "time_window": window_kind,
        "time_slug": time_slug,
        "topic_slug": intent.get("topic_slug", "general"),
        "date_range": dr,
        "span_days": span_days,
        "default_subtopics": subtopics,
        "report_prompt": (
            "结构：核心动态(3~5条) → 分主题详情 → 趋势或影响判断(1~2条)。"
            "每条要点标注来源URL。"
        ),
        "file_name": f"{time_slug}_{intent.get('topic_slug', 'general')}_{dr[0]:%Y-%m-%d}.md",
    }


def decompose(scenario: dict) -> list[tuple[str, str]]:
    """LLM 把主题 + 时间窗口拆成 3~5 个 (子问题, 搜索关键词)；失败回退默认拆分。

    返回 [(subtopic, keywords)]。keywords 是具体名词短语（如"大模型 发布 新品"），
    Worker 用它搜索而非整句，避免学术化子问题搜不到相关内容。
    """
    sys_p = (
        "你是调研规划员。把用户的调研主题拆成 3~5 个互不重叠、可独立搜索的子问题。\n"
        f"时间范围限定：{time_expr(scenario['date_range'])}，只关注该时间段内的信息。\n"
        f"参考拆分角度（可保留或调整）：{json.dumps(scenario['default_subtopics'], ensure_ascii=False)}\n"
        "输出 JSON 数组，每项：{\"subtopic\": \"子问题中文描述\", \"keywords\": \"搜索关键词（3~6个具体名词，空格分隔，不要句子；如：大模型 发布 新品 / AI alignment safety）\"}\n"
        "keywords 必须具体、可搜索，不要用完整句子。只输出 JSON，不要任何解释。"
    )
    raw = call_llm(sys_p, scenario["topic"], temperature=0.3)
    subs = parse_json_llm(raw, fallback=scenario["default_subtopics"])
    # 防御：兼容 [{subtopic, keywords}] 与纯字符串数组两种格式
    result = []
    if isinstance(subs, list):
        for s in subs:
            if isinstance(s, dict) and s.get("subtopic"):
                result.append((s["subtopic"], (s.get("keywords") or "").strip()))
            elif isinstance(s, str) and s.strip():
                result.append((s.strip(), ""))
    if not result:
        result = [(s, "") for s in scenario["default_subtopics"][:5]]
    return result


def research_worker(item, scenario: dict) -> dict:
    """一个 Worker = 一个子问题：三层搜索 → 提炼 → 结构化 JSON。

    item 为 (subtopic, keywords) 或纯字符串。搜索用 keywords（无则用子问题清洗词）。
    搜索策略：
      1) Bing（关键词，热点/新闻类主力）
      2) HN 关键词（英文 topic_en，科研/技术类补充，带时间过滤）
      3) HN 热帖 + 少数派 RSS（通用补充，按 URL 去重）
    合并后交给 LLM 提炼；无可用信息时返回带 error 的结构化结果。
    """
    subtopic, keywords = item if isinstance(item, tuple) else (item, "")
    query = keywords or clean_time_words(subtopic)
    try:
        results = search(query)[:SEARCH_LIMIT]

        # 补充源：HN 关键词（科研/技术）+ HN 热帖 + 少数派（按 URL 去重）
        extra_sources = []
        if scenario.get("topic_en"):
            extra_sources += fetch_hackernews_query(scenario["topic_en"], days=max(scenario["span_days"], 1))
        if scenario["span_days"] <= 7:
            extra_sources += fetch_hackernews() + fetch_sspai()
        seen = {r["url"] for r in results}
        for extra in extra_sources:
            if extra["url"] and extra["url"] not in seen:
                results.append(extra)
                seen.add(extra["url"])
        results = results[:SEARCH_LIMIT + 6]

        if not results:
            return {"subtopic": subtopic, "key_points": [], "sources": [], "error": "无搜索结果"}

        context = "\n".join(
            f"[{i}] {r['title']}\nURL: {r['url']}\n{r.get('snippet', '')}"
            for i, r in enumerate(results, 1)
        )
        sys_p = (
            "你是调研员。基于给定的搜索结果，提炼该子问题在指定时间范围内的关键发现。要求：\n"
            "1. 只写搜索结果里出现的事实，不编造、不推测；\n"
            "2. 明确区分『已确认事实』和『传闻/未经证实』；\n"
            "3. 每条要点一句话，最多 5 条，注明日期（如『9月24日』）；没有明确日期的标注『日期不详』；\n"
            "4. 只输出 JSON：{\"subtopic\": \"...\", \"key_points\": [\"...\"], \"sources\": [\"url\"]}；\n"
            "5. sources 必须来自上面真实出现的 URL；\n"
            "6. 若搜索结果与子问题相关，即使没有近期新闻，也应提炼出与该主题相关的背景要点并注明日期；"
            "只有完全无关时才输出空数组。"
        )
        raw = call_llm(sys_p, f"子问题：{subtopic}\n\n搜索结果：\n{context}")
        data = parse_json_llm(raw, fallback={"key_points": [], "sources": []})
        return {
            "subtopic": subtopic,
            "key_points": data.get("key_points", [])[:5],
            "sources": [u for u in data.get("sources", [])[:5] if u.startswith("http")],
        }
    except Exception as e:
        return {"subtopic": subtopic, "key_points": [], "sources": [], "error": str(e)}


def synthesize(results: list[dict], scenario: dict) -> str:
    """合并去重、按场景格式组织成最终报告。"""
    sys_p = (
        "你是资深内容编辑。基于调研结果生成一份「" + scenario["title"] + "」。\n"
        "要求：\n"
        "1. " + scenario["report_prompt"] + "\n"
        "2. 去重：多个调研员重复的要点只保留一次；\n"
        "3. 每条要点用括号标注来源URL；\n"
        "4. 区分『已确认』与『传闻』；没有可靠来源的信息不写；\n"
        "5. 均衡呈现：每个子问题都要有内容覆盖，不要只突出某一来源；\n"
        "6. 不要重复大标题，直接从第一个章节开始写；\n"
        "7. 中文 Markdown，控制在 600 字以内。"
    )
    return call_llm(sys_p, json.dumps(results, ensure_ascii=False, indent=2), temperature=0.5)


def run_scenario(scenario: dict) -> str:
    """执行一次完整调研：拆解 → 并行 Worker → 汇总 → 写文件。"""
    print(f"[任务] {scenario['title']}（时间范围：{time_expr(scenario['date_range'])}）")
    print("[主Agent] 拆解任务...")
    subtopics = decompose(scenario)
    if not subtopics:  # 双保险：decompose 兜底失败时直接用默认拆分
        subtopics = [(s, "") for s in scenario["default_subtopics"][:5]]
    print(f"[主Agent] 子问题：{[s for s, _ in subtopics]}")

    print(f"[调度] 并行启动 {min(MAX_WORKERS, len(subtopics))} 个调研 Worker...")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(research_worker, item, scenario): item for item in subtopics}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            status = f"{len(r['key_points'])} 条要点" if r["key_points"] else f"无可用信息({r.get('error', '搜索结果不相关')})"
            print(f"[Worker] 完成：{r['subtopic']} → {status}")

    print("[汇总Agent] 生成报告...")
    report = synthesize(results, scenario)

    out_path = scenario["file_name"]
    # 避免标题重复：LLM 自带 "# 标题" 时不再叠加脚本标题
    report = report.lstrip()
    if report.startswith("# "):
        content = report + "\n"
    else:
        content = (
            f"# {scenario['title']}\n"
            f"时间范围：{time_expr(scenario['date_range'])}\n\n"
            f"{report}\n"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("[完成] 报告已保存：" + out_path)
    return out_path
