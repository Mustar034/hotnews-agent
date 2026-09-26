"""入口：对话循环 + 快捷命令。

运行：
    python -m hotnews            对话模式（推荐）
    python -m hotnews daily      快捷：今天全网热点
    python -m hotnews weekly     快捷：本周 AI 热点
    python -m hotnews monthly    快捷：本月 OpenAI 动态
"""
import sys

from . import facts, fun, hotlists, intent, research, weather
from .config import EXIT_WORDS, GREETING

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


def chat_loop() -> None:
    """对话主循环：退出 → 猜谜状态 → 日常查询 → 趣味 → LLM 意图解析 → 分派执行。"""
    print("[Agent] " + GREETING)
    state = {"riddle": None}  # 猜谜状态：None 或 {"q","a","hint"}
    while True:
        try:
            user_input = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Agent] 再见！")
            break
        if not user_input:
            continue
        if user_input in EXIT_WORDS or user_input.lower() in EXIT_WORDS:
            print("[Agent] 好的，再见！")
            break

        # 0) 猜谜进行中：除非是新命令，否则视为猜答案
        if state["riddle"] and not fun.looks_like_new_command(user_input):
            if any(w in user_input for w in ("不知道", "猜不出来", "放弃", "答案是什么", "揭晓", "不猜了", "想不出来")):
                print(f"[Agent] 谜底是「{state['riddle']['a']}」！{fun.random_face()} 还想再猜一个吗？")
                state["riddle"] = None
                continue
            if fun.check_answer(user_input, state["riddle"]["a"]):
                print(f"[Agent] 恭喜答对！答案是「{state['riddle']['a']}」{fun.random_face()}")
                state["riddle"] = None
                continue
            print(f"[Agent] 不对哦，再猜猜~ 提示：{state['riddle']['hint']} {fun.random_face()}")
            continue

        # 1) 趣味：讲笑话 / 猜谜语
        if any(w in user_input for w in ("讲个笑话", "讲笑话", "笑话", "搞笑", "逗我", "来个笑话")):
            print("[Agent] " + fun.tell_joke())
            continue
        if any(w in user_input for w in ("猜谜语", "猜个谜", "猜谜", "谜语", "出个谜", "来条谜语")):
            r = fun.new_riddle()
            state["riddle"] = r
            print(f"[Agent] 来猜个谜语：{r['q']} （不想猜了可以说\"放弃\"）{fun.random_face()}")
            continue

        # 2) 规则优先：日常查询（零 LLM 成本、秒回）
        daily = intent.match_daily_query(user_input)
        if daily:
            print("[Agent] " + answer_daily(daily))
            continue

        # 3) LLM 意图解析：research / hotlist / chat
        try:
            parsed = intent.parse_intent(user_input)
        except Exception as e:
            print(f"[Agent] 抱歉，我没能理解您的需求（{e}），换个说法试试？")
            continue

        # 闲聊（带颜文字）
        if parsed.get("intent") == "chat":
            try:
                reply = _chat_reply(user_input)
                print(f"[Agent] {reply} {fun.random_face()}")
            except Exception:
                print(f"[Agent] 好的，还有别的需要吗？{fun.random_face()}")
            continue

        # 榜单：历史窗口（昨天/前天等）无历史榜单 → 自动转为资讯调研
        if parsed.get("intent") == "hotlist":
            window = parsed.get("time_window", "今天")
            if window not in ("今天",):
                print(f"[Agent] 您要的是「{window}」的榜单，但热榜是实时快照、没有历史数据。我改用资讯调研，为您整理{window}的热点新闻...")
                research_intent = {
                    "intent": "research",
                    "topic": f"{window}热点新闻",
                    "topic_en": "",
                    "topic_slug": parsed.get("topic_slug", "hotlist"),
                    "time_window": window,
                    "title": f"{window}热点新闻整理",
                    "subtopics": ["国内热点事件", "国际热点事件", "科技动态", "财经动态"],
                }
                try:
                    out = research.run_scenario(research.build_scenario(research_intent))
                    print(f"[Agent] 完成！报告已保存：{out}。还有什么需要帮忙的吗？")
                except Exception as e:
                    print(f"[Agent] 抱歉，执行失败了：{e}。可以试试换个时间范围。")
                continue

            print("[Agent] 好的，正在为您抓取榜单，请稍候...")
            try:
                out = hotlists.run_hotlist(parsed)
                print(f"[Agent] 完成！榜单已保存：{out}。还有什么需要帮忙的吗？")
            except Exception as e:
                print(f"[Agent] 抱歉，榜单抓取失败了：{e}")
            continue

        # 资讯调研
        print("[Agent] 好的，正在为您整理，请稍候...")
        try:
            out = research.run_scenario(research.build_scenario(parsed))
            print(f"[Agent] 完成！报告已保存：{out}。还有什么需要帮忙的吗？")
        except Exception as e:
            print(f"[Agent] 抱歉，执行失败了：{e}。您可以换个主题试试。")


def _chat_reply(user_input: str) -> str:
    """闲聊回复（LLM 一句话）。"""
    from .llm import call_llm
    return call_llm(
        "你是友好的资讯调研助手，用一句话简短回应（不超过50字）。",
        user_input,
        temperature=0.7,
    )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in PRESET_INTENTS:
        intent_data = PRESET_INTENTS[sys.argv[1]]
        try:
            research.run_scenario(research.build_scenario(intent_data))
        except Exception as e:
            print(f"[Agent] 执行失败：{e}")
    else:
        chat_loop()


if __name__ == "__main__":
    main()
