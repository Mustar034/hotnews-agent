"""入口：对话循环 + 快捷命令。

运行：
    python -m hotnews            对话模式（推荐）
    python -m hotnews daily      快捷：今天全网热点
    python -m hotnews weekly     快捷：本周 AI 热点
    python -m hotnews monthly    快捷：本月 OpenAI 动态

Agent 能力（读懂目标/拆分计划/调用工具/记住上下文/反思纠错/判断何时结束）
由 agent_core.handle_task 的五阶段循环承载；本文件保留会话状态机
（退出词/猜谜/趣味）、会话记录（session_log）与退出流程（结束语/自动关终端）。
"""
import os
import subprocess
import sys
import time
from datetime import datetime

from . import agent_core, facts, fun, memory as memory_mod, weather
from .budget import get_budget
from .config import AUTO_CLOSE_TERMINAL, CLOSE_DELAY_SECONDS, EXIT_WORDS, GREETING
from .session_log import SessionLog
from .trace import get_trace

# 快捷命令（非对话模式）
PRESET_INTENTS = {
    "daily": {
        "intent": "research",
        "topic": "全网热点",
        "topic_en": "",
        "topic_slug": "general",
        "time_window": "今天",
        "title": "今日全网热点",
        "subtopics": ["国内热点事件", "国际热点事件", "科技动态", "财经动态"],
    },
    "weekly": {
        "intent": "research",
        "topic": "AI 人工智能热点新闻",
        "topic_en": "artificial intelligence",
        "topic_slug": "ai",
        "time_window": "本周",
        "title": "AI 热点周报",
        "subtopics": ["大模型与产品发布", "AI 行业融资与并购", "AI 政策与监管", "AI 前沿研究与争议"],
    },
    "monthly": {
        "intent": "research",
        "topic": "OpenAI 时事热点",
        "topic_en": "OpenAI",
        "topic_slug": "openai",
        "time_window": "本月",
        "title": "OpenAI 月度动态",
        "subtopics": ["产品发布与更新", "合作与融资动态", "管理层与人事变动", "争议与政策动态"],
    },
}


def answer_daily(q: dict) -> str:
    """把日常查询的结构化意图翻译成最终回答文本。"""
    if q["intent"] == "weekday":
        return facts.answer_weekday(q["day"])
    if q["intent"] == "today_date":
        return facts.answer_today_date()
    if q["intent"] == "countdown":
        return facts.answer_countdown(q["target"])
    if q["intent"] == "weather":
        city_kind = q.get("city_kind", "absent")
        if q.get("ask_rain"):
            return weather.rain_answer(q["city"], q["day"], city_kind)
        return weather.weather_answer(q["city"], q["day"], city_kind)
    return "这个问题我暂时还不会，您可以换个说法试试。"


def _hotlist_topic(parsed: dict) -> str:
    """榜单速递的标题主题：按来源平台命名，避免带上数量（如"前5"）。"""
    source = parsed.get("source", "")
    if "weibo" in source or "微博" in source:
        return "微博热榜"
    if "zhihu" in source or "知乎" in source:
        return "知乎热榜"
    if "bili" in source or "b站" in source:
        return "B站热榜"
    if "baidu" in source or "百度" in source:
        return "百度热榜"
    if "bing" in source or "必应" in source:
        return "今日热点"
    return "今日热点"


def _ask_report() -> bool:
    """速递输出后询问是否需要生成完整报告。回车默认生成；明确否定才跳过。"""
    try:
        ans = input("是否需要生成完整报告，以便查看详情？[回车=要 / 输入\"不要\"跳过] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return True
    if ans in ("不要", "不用", "不需要", "不用了", "否", "不", "no", "n", "不生成", "算了", "不用生成", "跳过"):
        return False
    return True


def chat_loop() -> None:
    """对话主循环：会话记录 + 状态机（退出/猜谜/趣味）+ 任务分派（agent_core 五阶段）。

    每次启动都是新一轮对话：新 SessionMemory、新会话文件夹。
    """
    print("[Agent] " + GREETING)
    slog = SessionLog()
    slog.attach()
    memory = memory_mod.SessionMemory()
    state = {"riddle": None}  # 猜谜状态：None 或 {"q","a","hint"}
    round_no = 0
    interrupted = False
    try:
        while True:
            try:
                user_input = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                interrupted = True
                break
            if not user_input:
                continue
            if user_input in EXIT_WORDS or user_input.lower() in EXIT_WORDS:
                break

            round_no += 1
            slog.begin_round(round_no, user_input)

            # 0) 猜谜进行中：除非是新命令，否则视为猜答案
            if state["riddle"] and not fun.looks_like_new_command(user_input):
                if any(w in user_input for w in ("不知道", "猜不出来", "放弃", "答案是什么", "揭晓", "不猜了", "想不出来")):
                    print(f"[Agent] 谜底是「{state['riddle']['a']}」！{fun.random_face()} 还想再猜一个吗？")
                    state["riddle"] = None
                    slog.end_round()
                    continue
                if fun.check_answer(user_input, state["riddle"]["a"]):
                    print(f"[Agent] 恭喜答对！答案是「{state['riddle']['a']}」{fun.random_face()}")
                    state["riddle"] = None
                    slog.end_round()
                    continue
                print(f"[Agent] 不对哦，再猜猜~ 提示：{state['riddle']['hint']} {fun.random_face()}")
                slog.end_round()
                continue

            # 1) 趣味：讲笑话 / 猜谜语
            if any(w in user_input for w in ("讲个笑话", "讲笑话", "笑话", "搞笑", "逗我", "来个笑话")):
                joke = fun.tell_joke()
                print("[Agent] " + joke)
                memory.remember_user(user_input)
                memory.remember_agent(joke)
                slog.end_round()
                continue
            if any(w in user_input for w in ("猜谜语", "猜个谜", "猜谜", "谜语", "出个谜", "来条谜语")):
                r = fun.new_riddle()
                state["riddle"] = r
                print(f"[Agent] 来猜个谜语：{r['q']} （不想猜了可以说\"放弃\"）{fun.random_face()}")
                slog.end_round()
                continue

            # 2) 任务类请求：交给 Agent 核心循环（理解→计划→执行→反思→完成）
            try:
                agent_core.handle_task(user_input, memory)
            except Exception as e:
                print(f"[Agent] 抱歉，处理出错了：{e}。可以换个说法试试。")
                get_trace().log("note", f"未捕获异常：{e}", status="error")
            slog.end_round()
    finally:
        slog.detach()
    _say_goodbye(slog, memory, interrupted)


def _print_session_summary(memory) -> None:
    """会话结束时的成本与轨迹摘要（可解释 + 成本可控的落点）。"""
    trace = get_trace()
    print("\n[摘要] " + trace.render_summary())
    print("[摘要] " + get_budget().summary())


def _say_goodbye(slog: SessionLog, memory, interrupted: bool = False) -> None:
    """统一退出路径：摘要 → LLM 结束语 → 保存记录 → 延时自动关闭终端。"""
    _print_session_summary(memory)
    note = "被用户中断（Ctrl+C / EOF）" if interrupted else "正常结束"
    folder = slog.finish(extra_note=note)
    print(f"\n[Agent] {_farewell()}")
    print(f"[Agent] 对话记录已保存至：{os.path.join(folder, '对话记录.md')}")
    _auto_close()


def _farewell() -> str:
    """生成一句有变化的结束语：LLM 生成（预算内）→ 失败降级模板库。

    成本可控：退出调用也经预算记账；预算耗尽/LLM 异常时静默降级，
    绝不让"再见"都说不出来。GBK 安全：LLM 输出经 gbk_safe 过滤。
    """
    from .llm import call_llm
    try:
        text = call_llm(
            "你是友好的资讯助手。请说一句简短、温暖、有变化的道别语（8~25 字），"
            "语气自然不重复，可带一个简单颜文字（如 ^_^、O(∩_∩)O）。"
            "不要使用 emoji，不要使用引号，不要提及任何内部细节。只输出这一句话。",
            "本次对话到此结束。",
            temperature=0.9,
        )
        text = fun.gbk_safe((text or "").strip().strip('"“”'))
        if 4 <= len(text) <= 40:
            return text
    except Exception:
        pass  # 预算超限 / 网络失败 → 降级模板库
    return fun.random_farewell()


def _auto_close() -> None:
    """结束语后延时自动关闭终端（Windows）。

    安全策略：
    - config.AUTO_CLOSE_TERMINAL=False 或环境变量 HOTNEWS_NO_AUTOCLOSE=1 时跳过（IDE/测试场景）；
    - 只关闭确实是终端宿主的父进程（cmd/powershell/pwsh/windowsterminal），
      避免误杀 IDE、资源管理器或其他程序。
    """
    if not AUTO_CLOSE_TERMINAL:
        return
    print(f"[Agent] 终端将在 {CLOSE_DELAY_SECONDS} 秒后自动关闭（Ctrl+C 可取消）...")
    try:
        time.sleep(CLOSE_DELAY_SECONDS)
    except KeyboardInterrupt:
        print("\n[Agent] 已取消自动关闭，终端保留。")
        os._exit(0)
    if sys.platform == "win32":
        _close_parent_terminal()
    else:
        os._exit(0)


def _close_parent_terminal() -> None:
    """关闭 Windows 终端宿主进程（仅当父进程确为终端时）。"""
    try:
        pid = os.getppid()
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        name = ""
        if out.strip():
            parts = out.strip().split('","')
            if parts:
                name = parts[0].lstrip('"').lower()
        if name in ("cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe"):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
    except Exception:
        pass
    finally:
        os._exit(0)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in PRESET_INTENTS:
        # 快捷命令：预置意图直接走核心循环（含预算、轨迹与会话记录）
        memory = memory_mod.SessionMemory()
        slog = SessionLog()
        slog.attach()
        slog.begin_round(1, f"快捷命令：{sys.argv[1]}")
        try:
            agent_core.run_preset(PRESET_INTENTS[sys.argv[1]], memory)
            print("[摘要] " + get_budget().summary())
        finally:
            slog.end_round()
            slog.detach()
        folder = slog.finish(extra_note="快捷命令")
        print(f"\n[Agent] 快捷任务已完成！对话记录已保存至：{os.path.join(folder, '对话记录.md')}")
        _auto_close()
    else:
        chat_loop()


if __name__ == "__main__":
    main()
