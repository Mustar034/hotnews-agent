"""Agent 核心循环：把一次用户请求显式地走完五阶段。

    UNDERSTAND  读懂目标：规则层（零成本）→ LLM 意图解析 → 信息不足时澄清追问
    PLAN        拆分计划：所有任务类型生成显式步骤（可展示、可解释、支持"只要前N项"）
    EXECUTE     调用工具：统一经 tools.run_tool 收口，记录执行轨迹
    REFLECT     反思纠错：检查覆盖率/失败步骤，预算允许时换关键词重试缺失子问题
    FINISH      判断何时结束：完成判定（达标 或 预算/轮次耗尽）→ 输出结果 → 记忆入库

设计约束：
    可控   —— 预算耗尽立即收敛；计划可截断；用户随时可换说法重新理解
    可解释 —— 每阶段打印轨迹；结束输出轨迹摘要与覆盖率
    安全   —— LLM 收到外部抓取内容一律带注入防护声明（guard=True）
    成本可控 —— 规则优先、能代码绝不 LLM；所有 LLM 调用经预算记账

与 main.py 的分工：退出词/猜谜状态机/趣味互动留在 main（会话状态机），
本模块只处理"任务类"请求（调研/榜单/天气/日期/聊天）。
"""
import re

from . import digest, intent, research
from .budget import BudgetExceeded, get_budget
from .config import MAX_REFLECT_ROUNDS
from .events import say
from .trace import get_trace
from .tools import describe_tool

# 触发"只要前N项/只做前N项"的截断模式
_TRIM_PAT = re.compile(r"(?:只要|只做|只查|只要前|只做前)\s*([一二三四五六七八九十\d]+)\s*(?:项|条|个|步)?")


def _trim_request(text: str) -> int:
    """识别"只要前 2 项"类指令，返回截断数量；0 表示不截断。"""
    m = _TRIM_PAT.search(text)
    if not m:
        return 0
    s = m.group(1)
    cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return cn.get(s, int(s) if s.isdigit() else 0)


# ---------------------------------------------------------------------------
# UNDERSTAND
# ---------------------------------------------------------------------------
def _understand(user_input: str, memory) -> dict:
    """第一阶段：读懂目标。返回统一的任务描述 dict。

    - 承前省略句（"西安呢？"承接上轮天气）→ 规则补全，零 LLM 成本
    - 规则层命中（天气/周几/日期/倒计时）→ 确定性任务，零 LLM 成本
    - 未命中 → LLM 意图解析（research / hotlist / chat）
    - 信息不足以执行 → 返回 {"intent": "clarify", "question": ...}，由上层追问
    """
    trace = get_trace()

    # 0) 承前省略句补全："西安呢？" → 西安天气（复用上轮日期/雨雪参数）
    resolved, elliptical = intent.resolve_ellipsis(user_input, memory)
    if elliptical:
        trace.log("understand",
                  f"省略句补全：{user_input.strip()} → {elliptical['intent']}"
                  f"({elliptical.get('city', '')} {elliptical.get('day', '')})")
        # 天气/周几等确定性查询统一包成 daily 任务（与规则层结构一致）
        if elliptical.get("intent") in ("weather", "weekday"):
            return {"intent": "daily", "daily": elliptical}
        return elliptical
    if resolved:
        trace.log("understand", f"省略句补全：{user_input.strip()} → {resolved[:40]}")
        user_input = resolved

    daily = intent.match_daily_query(user_input)
    if daily:
        trace.log("understand", f"规则命中：{daily['intent']}",
                  detail=f"城市={daily.get('city','')} 天={daily.get('day','')}")
        return {"intent": "daily", "daily": daily}

    trace.log("understand", "LLM 意图解析")
    try:
        parsed = intent.parse_intent(user_input, memory=memory)
    except BudgetExceeded as e:
        raise
    except Exception as e:
        trace.log("understand", f"解析失败：{e}", status="error")
        return {"intent": "clarify",
                "question": f"抱歉，我没能完全理解您的需求（{e}）。可以换个说法，或直接告诉我："
                            "想调研什么主题？想看哪个平台的榜单？"}

    if parsed.get("intent") == "clarify":
        trace.log("understand", "信息不足，需要澄清", status="note")
        return parsed

    trace.log("understand",
              f"意图={parsed['intent']}",
              detail=f"主题={parsed.get('topic', '')} 窗口={parsed.get('time_window', '')}")
    return parsed


def _clarify_question() -> str:
    """澄清追问文案（一次性问完，给选项，避免反复打扰）。"""
    return (
        "我理解您想让我做资讯调研，但还需要明确一点：您想调研什么主题？\n"
        "  例：AI 热点 / 生物科研 / OpenAI 动态 / 全网热点\n"
        "（直接回复主题即可；回「全网热点」我会按综合热点调研）"
    )


# ---------------------------------------------------------------------------
# PLAN
# ---------------------------------------------------------------------------
def _plan(parsed: dict, user_input: str, memory) -> list[dict]:
    """第二阶段：拆分计划。返回计划步骤列表 [{"step", "tool", "desc"}, ...]。

    research 类型的拆解（decompose，含搜索关键词）只做一次，结果挂在
    parsed["_subtopics_raw"] 上，供 EXECUTE 阶段复用，避免重复调用 LLM。
    """
    trace = get_trace()
    trim = _trim_request(user_input)

    if parsed["intent"] == "chat":
        plan = [{"step": 1, "tool": "llm_chat", "desc": "一句话简短回复"}]
    elif parsed["intent"] == "hotlist":
        plan = [{"step": 1, "tool": "fetch_hotlist",
                 "desc": f"抓取榜单（来源={parsed.get('source') or '综合'}，{parsed.get('count', 10)} 条）"}]
    elif parsed["intent"] == "daily":
        plan = [{"step": 1, "tool": "daily", "desc": "确定性计算/抓取"}]
    else:  # research
        # decompose 需要 build_scenario 的产物（date_range 等）；build 是纯计算，零 LLM 成本
        scenario = research.build_scenario(parsed)
        subtopics = research.decompose(scenario)  # [(subtopic, keywords)]，一次拆解
        if not subtopics:
            subtopics = [(s, "") for s in parsed.get("subtopics") or ["热点事件", "行业动态"]]
        parsed["_subtopics_raw"] = subtopics
        plan = [
            {"step": i + 1, "tool": "research_worker", "desc": f"调研：{s}"}
            for i, (s, _k) in enumerate(subtopics)
        ]

    if trim and trim < len(plan):
        plan = plan[:trim]

    trace.log("plan", f"计划拆分：{len(plan)} 步")
    for p in plan:
        trace.log("plan", p["desc"], tool=p["tool"])
    return plan


def _show_plan(plan: list[dict], parsed: dict, sink=None) -> None:
    """把计划展示给用户（可解释、可控）。"""
    if parsed["intent"] == "chat":
        return  # 聊天单步，不啰嗦
    lines = [f"[计划] 我将分 {len(plan)} 步完成："]
    for p in plan:
        lines.append(f"  {p['step']}. {p['desc']}")
    say(sink, "\n".join(lines), "plan", steps=plan)


# ---------------------------------------------------------------------------
# EXECUTE + REFLECT + FINISH
# ---------------------------------------------------------------------------
def _run_daily(daily: dict, memory, sink=None) -> None:
    """确定性任务（天气/周几/日期/倒计时）：单步执行，零 LLM。"""
    trace = get_trace()
    from .main import answer_daily  # 延迟导入避免循环依赖
    ans = answer_daily(daily)
    trace.log("tool", "answer_daily", detail=daily["intent"], status="ok")
    trace.log("finish", "确定性任务完成（零 LLM 成本）")
    memory.remember_agent(ans)
    memory.remember_fact("last_query_intent", daily)  # 供下一轮省略句补全
    say(sink, f"[Agent] {ans}", "agent")


def _run_chat(user_input: str, memory, sink=None) -> None:
    """chat 意图：一句话闲聊（受会话预算约束）。"""
    from .llm import call_llm
    try:
        reply = call_llm(
            "你是友好的资讯调研助手，用一句话简短回应（不超过50字）。",
            user_input,
            temperature=0.7,
        )
    except BudgetExceeded as e:
        say(sink, f"[Agent] {e}", "error")
        return
    except Exception:
        reply = "好的，还有别的需要吗？"
    from .fun import random_face
    memory.remember_agent(reply)
    say(sink, f"[Agent] {reply} {random_face()}", "agent")


def _run_hotlist(parsed: dict, memory, sink=None, interactive: bool = True) -> None:
    """hotlist 任务：抓榜单 → 速递 → 询问完整报告。"""
    from . import hotlists
    trace = get_trace()

    window = parsed.get("time_window", "今天")
    if window not in ("今天",):
        # 热榜无历史 → 自动转为资讯调研（现有行为，显式记录原因）
        trace.log("reflect", f"榜单无历史数据（{window}），转为资讯调研", status="note")
        say(sink, f"[Agent] 您要的是「{window}」的榜单，但热榜是实时快照、没有历史数据。我改用资讯调研，为您整理{window}的热点新闻...", "agent")
        research_intent = {
            "intent": "research",
            "topic": f"{window}热点新闻",
            "topic_en": "",
            "topic_slug": parsed.get("topic_slug", "hotlist"),
            "time_window": window,
            "title": f"{window}热点新闻整理",
            "subtopics": ["国内热点事件", "国际热点事件", "科技动态", "财经动态"],
        }
        _run_research(research_intent, memory, sink=sink, interactive=interactive)
        return

    say(sink, "[Agent] 好的，正在为您抓取榜单，请稍候...", "progress")
    try:
        data = hotlists.fetch_hotlist(parsed)
        trace.log("tool", f"fetch_hotlist（{parsed.get('source') or '综合'}）",
                  detail=f"{len(data.get('sections', []))} 个数据源", status="ok")
    except Exception as e:
        trace.log("tool", f"fetch_hotlist 失败：{e}", status="error")
        say(sink, f"[Agent] 抱歉，榜单抓取失败了：{e}", "error")
        return

    from .main import _ask_report, _hotlist_topic
    say(sink, digest.make_hotlist_digest(data, _hotlist_topic(parsed)), "digest")
    if not data["sections"]:
        out = hotlists.write_hotlist(data)
        trace.log("finish", f"无数据，失败留档：{out}", status="note")
        say(sink, f"[Agent] 抱歉，所有热榜数据源都暂时不可用，已留档：{out}", "error")
        return
    if not interactive or _ask_report():
        out = hotlists.write_hotlist(data)
        memory.remember_fact("last_report", out)
        memory.remember_fact("last_query_intent", {
            "intent": "hotlist", "source": parsed.get("source", ""),
            "time_window": parsed.get("time_window", "今天"),
        })
        trace.log("finish", f"报告已保存：{out}")
        say(sink, f"[Agent] 完成！榜单报告已保存：{out}。还有什么需要帮忙的吗？", "agent")
    else:
        say(sink, "[Agent] 好的，速递就到这里。还有什么需要帮忙的吗？", "agent")


def _run_research(parsed: dict, memory, sink=None, is_reflect: bool = False,
                  interactive: bool = True) -> None:
    """research 任务：拆解 → 并行执行 → 覆盖率检查（反思）→ 汇总 → 完成判定。"""
    trace = get_trace()

    try:
        scenario = research.build_scenario(parsed)
        trace.enter_task("research", scenario["title"])
        trace.log("understand", f"调研主题：{scenario['topic']}（{scenario['time_window']}）")

        # EXECUTE：复用 PLAN 阶段已拆解的子问题（含搜索关键词），避免重复调 LLM
        subtopics = parsed.get("_subtopics_raw") or research.decompose(parsed)
        if not subtopics:
            subtopics = [(s, "") for s in scenario["default_subtopics"][:5]]
        scenario["_subtopics"] = subtopics

        result = research.run_research(scenario, sink=sink)
    except BudgetExceeded as e:
        say(sink, f"[Agent] {e}", "error")
        return
    except Exception as e:
        say(sink, f"[Agent] 抱歉，执行失败了：{e}。您可以换个主题试试。", "error")
        return

    # REFLECT：覆盖率检查（仅首轮；重试轮不再递归反思，保证终止）
    if not is_reflect:
        _reflect_research(scenario, result, memory, sink=sink)

    # FINISH：输出速递 → 询问完整报告 → 记忆入库
    say(sink, result["digest"], "digest")
    if not result["report"]:
        # 预算超限或汇总失败：只给速递，不生成报告
        say(sink, "[Agent] （本次未生成完整报告：LLM 预算超限或汇总失败。速递已给出。）还有什么需要帮忙的吗？", "agent")
        return
    from .main import _ask_report
    if not interactive or _ask_report():
        out = research.save_report(scenario, result["report"], sink=sink)
        memory.remember_fact("last_report", out)
        memory.remember_fact("last_topic", scenario["topic"])
        memory.remember_fact("last_query_intent", {
            "intent": "research", "topic": scenario["topic"],
            "time_window": scenario["time_window"],
        })
        trace.log("finish", f"报告已保存：{out}")
        say(sink, f"[Agent] 完成！报告已保存：{out}。还有什么需要帮忙的吗？", "agent")
    else:
        say(sink, "[Agent] 好的，速递就到这里。还有什么需要帮忙的吗？", "agent")


def _reflect_research(scenario: dict, result: dict, memory, sink=None) -> None:
    """第四阶段：反思纠错。检查子问题覆盖率，预算允许时换关键词重试缺失项。

    完成判定：全部子问题都有要点 → 达标；否则在预算与轮次上限内重试缺失项。
    """
    trace = get_trace()
    budget = get_budget()

    covered = [r for r in result["_workers"] if r.get("key_points")]
    missing = [r for r in result["_workers"] if not r.get("key_points")]
    if not missing:
        trace.log("reflect", f"覆盖率达标：{len(covered)}/{len(result['_workers'])} 个子问题有要点")
        say(sink, f"[反思] 覆盖率 {len(covered)}/{len(result['_workers'])}，全部子问题均有要点，无需重试。", "reflection")
        return

    trace.log("reflect",
              f"覆盖率不足：{len(covered)}/{len(result['_workers'])}",
              detail="缺失子问题：" + "、".join(r["subtopic"][:20] for r in missing),
              status="note")
    say(sink, f"[反思] 覆盖率 {len(covered)}/{len(result['_workers'])}，{len(missing)} 个子问题未获取到要点。", "reflection")
    if len(missing) >= len(result["_workers"]):
        # 全部失败：不无脑重试（可能是网络/源故障），直接收敛并说明
        trace.log("finish", "全部子问题无结果，收敛输出（可能是数据源故障）", status="note")
        say(sink, "[Agent] 本次调研所有子问题都未获取到可靠信息（可能是搜索源临时故障），已按现有结果输出，您也可以换个主题重试。", "agent")
        return
    if not budget.in_task or budget.remaining <= 0:
        trace.log("finish", "预算不足，跳过反思重试", status="note")
        return

    # 换关键词重试缺失项（最多 MAX_REFLECT_ROUNDS 轮，每轮只重试缺失项）
    for rnd in range(1, MAX_REFLECT_ROUNDS + 1):
        retry_items = [(r["subtopic"], "") for r in missing]
        trace.log("reflect", f"第 {rnd} 轮反思：重试 {len(retry_items)} 个缺失子问题（换更宽泛关键词）")
        say(sink, f"[反思] 第 {rnd} 轮反思：{len(missing)} 个子问题未获取到可靠信息，换更宽泛关键词重试...", "reflection")
        try:
            new_results = research.retry_missing(scenario, retry_items, sink=sink)
        except BudgetExceeded:
            trace.log("finish", "反思重试超出预算，收敛", status="note")
            break
        except Exception as e:
            trace.log("reflect", f"重试失败：{e}", status="error")
            break
        recovered = [r for r in new_results if r.get("key_points")]
        if recovered:
            result["_workers"] = [r for r in result["_workers"] if r.get("key_points")] + new_results
            # 重新生成汇总与速递
            try:
                scenario2 = dict(scenario)
                result["report"] = research.synthesize(result["_workers"], scenario2)
                result["digest"] = digest.make_research_digest(result["_workers"], scenario2)
                trace.log("reflect", f"重试恢复 {len(recovered)} 个子问题，已重新汇总")
                say(sink, f"[反思] 重试恢复 {len(recovered)} 个子问题，已重新汇总。", "reflection")
            except Exception:
                trace.log("reflect", "重新汇总失败，沿用首轮结果", status="error")
        break  # 反思只跑 1 轮，保证终止
    trace.log("finish", "反思结束，收敛输出")


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------
def run_preset(intent_data: dict, memory, sink=None) -> None:
    """快捷命令入口（daily/weekly/monthly）：预置意图直接执行，含预算与轨迹。"""
    trace = get_trace()
    trace.enter_task("research", intent_data.get("title", "快捷调研"))
    try:
        scenario = research.build_scenario(intent_data)
        result = research.run_research(scenario, sink=sink)
        out = research.save_report(scenario, result["report"], sink=sink)
        memory.remember_fact("last_report", out)
        memory.remember_fact("last_topic", scenario["topic"])
        memory.remember_fact("last_query_intent", {
            "intent": "research", "topic": scenario["topic"],
            "time_window": scenario["time_window"],
        })
        trace.log("finish", f"报告已保存：{out}")
        say(sink, f"[Agent] 完成！报告已保存：{out}", "agent")
    except BudgetExceeded as e:
        say(sink, f"[Agent] {e}", "error")
    except Exception as e:
        say(sink, f"[Agent] 执行失败：{e}", "error")
    finally:
        trace.finish_task()


def handle_task(user_input: str, memory, sink=None, interactive: bool = True) -> None:
    """处理一轮任务类请求（main 已排除退出/猜谜/趣味）。

    每轮重新走五阶段：理解 → 计划 → 执行 → 反思 → 完成。

    sink：事件输出接收器（None 时打印到终端，行为不变）
    interactive：True 时调研/榜单完成后询问"是否生成完整报告"（终端）；
                 False 时直接生成（网页/微信等无输入交互的入口）。
    """
    trace = get_trace()
    budget = get_budget()
    trace.enter_task("handle", user_input[:30])
    memory.remember_user(user_input)
    budget.enter_task("task")  # 任务级预算记账（chat/daily 也计入，防失控）
    try:
        _handle_task_inner(user_input, memory, sink=sink, interactive=interactive)
    finally:
        budget.exit_task()


def _handle_task_inner(user_input: str, memory, sink=None, interactive: bool = True) -> None:
    trace = get_trace()

    # 指代解析：把「第二条/刚才那个/那个报告」补全为记忆中的对象
    resolved_input = memory.resolve_reference(user_input)

    # UNDERSTAND
    try:
        parsed = _understand(resolved_input, memory)
    except BudgetExceeded as e:
        say(sink, f"[Agent] {e}", "error")
        return

    if parsed.get("intent") == "clarify":
        say(sink, f"[Agent] {parsed.get('question') or _clarify_question()}", "agent")
        return

    # PLAN（research 的拆解在 _plan 内完成）
    plan = _plan(parsed, resolved_input, memory)
    _show_plan(plan, parsed, sink=sink)

    # 执行与收敛
    if parsed["intent"] == "chat":
        _run_chat(resolved_input, memory, sink=sink)
    elif parsed["intent"] == "daily":
        _run_daily(parsed["daily"], memory, sink=sink)
    elif parsed["intent"] == "hotlist":
        _run_hotlist(parsed, memory, sink=sink, interactive=interactive)
    else:
        parsed["_user_input"] = resolved_input
        _run_research(parsed, memory, sink=sink, interactive=interactive)

    trace.finish_task()
    say(sink, "[摘要] " + trace.render_summary(), "summary")
