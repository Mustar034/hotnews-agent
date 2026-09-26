"""热点资讯对话 Agent 包。

结构：
    main.py       入口：对话循环、命令分发
    config.py     配置（API key、模型、城市表、问候语）
    llm.py        DeepSeek 调用封装
    time_utils.py 时间窗口计算（调研用）
    search.py     多源搜索（Bing / 少数派 / Hacker News）
    hotlists.py   热榜抓取（百度 / B站）与榜单报告
    weather.py    天气查询（中国天气网 7 天预报）
    facts.py      日常事实查询（周几 / 倒计时 / 城市识别）
    intent.py     意图解析（规则优先 + LLM 兜底）
    research.py   调研引擎（拆解 / Worker / 汇总）

运行：
    python -m hotnews            对话模式（推荐）
    python -m hotnews daily      快捷：今天全网热点
    python -m hotnews weekly     快捷：本周 AI 热点
    python -m hotnews monthly    快捷：本月 OpenAI 动态
"""
