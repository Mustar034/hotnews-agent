"""调研引擎：拆解 → 并行 Worker → 汇总 → 速递/报告。

流程：build_scenario 组装场景 → decompose 拆子问题 → ThreadPoolExecutor
并行跑 research_worker（每 Worker 三层搜索 + LLM 提炼）→ synthesize 汇总。
run_research 返回速递+完整报告文本（不写文件），save_report 负责落盘。

反思纠错支持：run_research 返回 worker 明细（_workers）供上层做覆盖率检查；
retry_missing 对缺失子问题用更宽泛关键词重搜重提炼（受预算约束）。

时间窗口纪律（重要）：
- 单日窗口（今天/昨天/前天）：提炼规则强制只留"当天"要点，另有确定性日期过滤
  _filter_single_day 做兜底——不依赖 LLM 自觉，避免把前几天旧闻混进"今天"速递；
- 多日窗口：保留"超过范围开始3天标背景"的宽松规则。
"""
import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from . import digest
from .budget import BudgetExceeded
from .config import MAX_WORKERS, SEARCH_LIMIT
from .events import say
from .llm import call_llm, call_llm_json, parse_json_llm
from .search import fetch_hackernews, fetch_hackernews_query, fetch_sspai, search
from .time_utils import clean_time_words, date_range_of, normalize_window, point_date, time_expr

# 单日窗口：key_points 里出现这些字样直接剔除（LLM 标记的背景条目）
_BACKGROUND_MARKERS = ("背景，非本期", "背景,非本期", "非本期", "背景条目")


def _filter_single_day(results: list[dict], window_date: datetime,
                       allow_prev_days: int = 1) -> list[dict]:
    """确定性兜底：单日窗口只保留『当天』要点，剔除旧日期与背景标注。

    两级策略（解决"周一早上当天新闻少导致全空"的现实问题）：
      1. 严格：凡要点中解析出日期且不是 window_date，或含背景标记，一律剔除；
         没有明确日期的要点也剔除（单日窗口无法证明它是当天的，宁缺毋滥）；
      2. 若严格后所有 worker 的当天要点为 0，放宽到最近 allow_prev_days 天
         （保留明确日期在窗口日±[0,allow_prev_days] 内的要点），并给每个
         result 附加 _single_day_note="relaxed"，供速递如实标注"含最近N天"。
    不依赖 LLM 自觉（LLM 提炼规则可能被绕过）。
    """
    want = f"{window_date:%Y-%m-%d}"
    relaxed_min = f"{(window_date - timedelta(days=allow_prev_days)):%Y-%m-%d}"

    def clean(r: dict, lo: str, hi: str) -> dict:
        kps = []
        for kp in r.get("key_points", []):
            if any(mark in kp for mark in _BACKGROUND_MARKERS):
                continue
            d = point_date(kp)
            if d is None:
                continue
            if lo <= d <= hi:
                kps.append(kp)
        return {**r, "key_points": kps[:5]}

    strict = [clean(r, want, want) for r in results]
    if any(r["key_points"] for r in strict):
        return strict
    # 放宽：保留明确日期在最近 allow_prev_days 天内的要点
    relaxed = [clean(r, relaxed_min, want) for r in results]
    for r in relaxed:
        r["_single_day_note"] = "relaxed"
    return relaxed


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
    # JSON 解析失败会自愈重试（call_llm_json），失败耗尽才回退默认拆分
    subs = call_llm_json(sys_p, scenario["topic"], temperature=0.3,
                         fallback=scenario["default_subtopics"])
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
        # 单日过滤只认语义窗口（今天/昨天/前天）；"本周"即使恰好只有一天
        # （如周一时本周==今天）也按多日宽松规则，避免把本周热点全部误杀
        is_single_day = scenario["time_window"] in ("今天", "昨天", "前天")
        if is_single_day:
            time_rule = (
                f"4. 调研时间范围是：{time_expr(scenario['date_range'])}（单日）。"
                "只提炼『该日当天』发布的要点，每条要点必须带有该日日期（如『10月5日』）；"
                "日期不是当天的条目（哪怕是前一天）一律不得写入 key_points，"
                "也不得作为本期热点；来源可保留在 sources 供完整报告引用；\n"
            )
        else:
            time_rule = (
                f"4. 调研时间范围是：{time_expr(scenario['date_range'])}。优先提炼该范围内的信息；"
                "日期明显早于范围开始（超过3天）的要点，结尾必须标注『（背景，非本期）』，不能作为本期热点核心；\n"
            )
        sys_p = (
            "你是调研员。基于给定的搜索结果，提炼该子问题在指定时间范围内的关键发现。要求：\n"
            "1. 只写搜索结果里出现的事实，不编造、不推测；\n"
            "2. 明确区分『已确认事实』和『传闻/未经证实』；\n"
            "3. 每条要点一句话，最多 5 条，注明日期（如『9月24日』）；没有明确日期的标注『日期不详』；\n"
            + time_rule +
            "5. 只输出 JSON：{\"subtopic\": \"...\", \"key_points\": [\"...\"], \"sources\": [\"url\"]}；\n"
            "6. sources 必须来自上面真实出现的 URL；\n"
            "7. 若搜索结果与子问题相关，即使没有近期新闻，也应提炼出与该主题相关的背景要点并注明日期；"
            "只有完全无关时才输出空数组。注意：平台登录页/功能页/客服页/无关产品页等与主题无关的内容，必须视为无关并输出空数组，"
            "不得硬凑要点。"
        )
        # guard=True：搜索结果来自外部网页，必须带注入防护声明
        raw = call_llm(sys_p, f"子问题：{subtopic}\n\n搜索结果：\n{context}", guard=True)
        data = parse_json_llm(raw, fallback={"key_points": [], "sources": []})
        result = {
            "subtopic": subtopic,
            "key_points": data.get("key_points", [])[:5],
            "sources": [u for u in data.get("sources", [])[:5] if u.startswith("http")],
        }
        # 单日窗口确定性兜底：只留当天要点（不依赖 LLM 自觉）
        if scenario["time_window"] in ("今天", "昨天", "前天"):
            result = _filter_single_day([result], scenario["date_range"][0])[0]
        return result
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


def run_research(scenario: dict, sink=None) -> dict:
    """执行一次完整调研：拆解（或复用已拆解子问题）→ 并行 Worker → 汇总。

    返回 {"digest", "report", "file_name", "_workers"}（不写文件）。
    _workers 为各子问题明细，供上层反思纠错（覆盖率检查）。
    预算超限（BudgetExceeded）时按已完成的 worker 部分收敛，不中断进程。

    sink：事件输出接收器；None 时打印到终端（与改造前行为一致）。
    """
    say(sink, f"[任务] {scenario['title']}（时间范围：{time_expr(scenario['date_range'])}）", "progress")
    say(sink, "[主Agent] 拆解任务...", "progress")
    if scenario.get("_subtopics"):
        subtopics = scenario["_subtopics"]
        say(sink, "[主Agent] 复用已拆解的子问题（不重复调用 LLM）", "progress")
    else:
        subtopics = decompose(scenario)
    if not subtopics:  # 双保险：decompose 兜底失败时直接用默认拆分
        subtopics = [(s, "") for s in scenario["default_subtopics"][:5]]
    say(sink, f"[主Agent] 子问题：{[s for s, _ in subtopics]}", "progress")

    say(sink, f"[调度] 并行启动 {min(MAX_WORKERS, len(subtopics))} 个调研 Worker...", "progress")
    results = []
    partial = False
    # 每个 Worker 任务各自复制当前会话上下文（contextvars.Context 不能重复 run）
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(contextvars.copy_context().run, research_worker, item, scenario): item
            for item in subtopics
        }
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except BudgetExceeded:
                say(sink, "[调度] LLM 预算超限，按已完成的 Worker 部分收敛（不中断）", "progress")
                partial = True
                break
            results.append(r)
            status = f"{len(r['key_points'])} 条要点" if r["key_points"] else f"无可用信息({r.get('error', '搜索结果不相关')})"
            say(sink, f"[Worker] 完成：{r['subtopic']} → {status}", "progress")

    say(sink, "[汇总Agent] 生成报告...", "progress")
    try:
        report = synthesize(results, scenario)
    except BudgetExceeded:
        say(sink, "[汇总Agent] 预算超限，跳过 LLM 汇总，输出速递兜底", "progress")
        report = ""
        partial = True

    return {
        "digest": digest.make_research_digest(results, scenario),
        "report": report,
        "file_name": scenario["file_name"],
        "_workers": results,
        "partial": partial,
    }


def retry_missing(scenario: dict, items: list[tuple[str, str]], sink=None) -> list[dict]:
    """反思纠错：对缺失子问题用更宽泛的关键词重搜、重提炼。

    items 为 [(subtopic, keywords)]，keywords 为空时用更宽泛策略：
    - 去掉子问题里的修饰词，取核心名词；
    - 加时间窗口词（如"2026年10月"）帮助搜索引擎定位。
    返回与 research_worker 同结构的列表。受预算约束（LLM 提炼调用照常记账）。
    """
    broadened = []
    for subtopic, _k in items:
        core = clean_time_words(subtopic)
        # 更宽泛：去掉"最新/热点/关注/进展"等限定词，保留实体词
        for w in ("最新", "热点", "资讯", "新闻", "关注", "进展", "动态", "情况", "现状", "影响", "趋势"):
            core = core.replace(w, "")
        core = core.strip() or subtopic
        broadened.append((subtopic, core))
    results = []
    for item in broadened:
        # 每次单独复制上下文（Context 不能重复 run）
        r = contextvars.copy_context().run(research_worker, item, scenario)
        results.append(r)
        status = f"{len(r['key_points'])} 条要点" if r["key_points"] else "仍无可用信息"
        say(sink, f"[Worker-重试] {r['subtopic']} → {status}", "progress")
    return results


def save_report(scenario: dict, report: str, sink=None) -> str:
    """把调研报告文本写入 md 文件，返回文件路径。"""
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
    say(sink, "[完成] 报告已保存：" + out_path, "progress")
    return out_path
