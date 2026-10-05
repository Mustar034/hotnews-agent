"""热点资讯对话 Agent 包。

Agent 能力（六项）：
    读懂目标  —— intent 规则层 + LLM 层 + 澄清出口（信息不足追问）
    拆分计划  —— agent_core.PLAN：所有任务生成显式步骤并展示
    调用工具  —— tools 注册表统一收口，记录执行轨迹
    记住上下文 —— memory：对话历史/事实记忆/指代解析（"第二条""那个报告"）
    反思纠错  —— REFLECT：覆盖率检查 + 换关键词重试（有界）
    判断何时结束 —— FINISH：达标或预算/轮次耗尽 → 收敛输出

四约束：
    可控    —— 预算上限、计划截断、随时可换说法
    可解释  —— 执行轨迹、覆盖率、成本统计均可见
    安全    —— 无内置 Key、外部内容注入防护、文件名安全
    成本可控 —— 规则优先 + LLM 调用计数 + 会话/任务两级预算

结构：
    main.py       入口：会话状态机、会话记录、退出流程（结束语/自动关终端）
    agent_core.py Agent 核心循环（五阶段）
    session_log.py 会话记录（markdown 存档，文件夹按开始~结束时间命名）
    config.py     配置（API key、模型、预算、重试、会话记录）
    budget.py     LLM 调用预算与成本统计
    memory.py     会话记忆与指代解析
    trace.py      执行轨迹
    tools.py      工具注册表
    llm.py        DeepSeek 调用封装（记账/重试/防护）
    intent.py     意图解析（规则优先 + LLM 兜底 + 澄清）
    time_utils.py 时间窗口计算
    search.py     多源搜索（Bing / 少数派 / Hacker News）
    research.py   调研引擎（拆解 / Worker / 覆盖率 / 汇总 / 反思重试）
    hotlists.py   热榜抓取（百度 / B站 / 微博 / 知乎）
    weather.py    天气查询（中国天气网 7 天预报）
    facts.py      日常事实查询（周几 / 倒计时 / 城市识别）
    fun.py        趣味功能（笑话 / 谜语 / 颜文字）

运行：
    python -m hotnews            对话模式（推荐）
    python -m hotnews daily      快捷：今天全网热点
    python -m hotnews weekly     快捷：本周 AI 热点
    python -m hotnews monthly    快捷：本月 OpenAI 动态
"""
