"""会话记忆：对话历史 + 事实记忆 + 指代解析。

记住上下文的落点：
- 对话历史：保留最近 N 轮（角色 + 内容摘要），供意图解析/闲聊参考；
- 事实记忆：会话内的关键事实（最近主题、最近报告路径、用户提及的城市、上一次榜单等）；
- 指代解析：把「第二条 / 刚才那个 / 那个报告 / 它」等指代词解析为记忆中的具体对象，
  使多轮对话（"再详细讲讲第二条"）可被理解。零 LLM 成本，纯规则。

用法：
    mem = SessionMemory()
    mem.remember_user("本周 AI 热点")
    mem.remember_fact("last_report", "weekly_ai_2026-09-21.md")
    resolved = mem.resolve_reference("第二条")   # -> "本周 AI 热点中的第 2 条"
"""
import re
from datetime import datetime

HISTORY_LIMIT = 12  # 最多保留轮数（超出丢最旧）


class SessionMemory:
    def __init__(self):
        self.history: list[dict] = []          # {"role", "content", "ts"}
        self.facts: dict = {}                  # 事实记忆（键为固定名称）
        self.last_lists: list[list[str]] = []  # 最近的列表型结果（榜单/速递条目），供"第N条"指代

    # ---- 写入 ----
    def remember_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text, "ts": datetime.now().strftime("%H:%M:%S")})
        self._trim()

    def remember_agent(self, text: str) -> None:
        self.history.append({"role": "agent", "content": text, "ts": datetime.now().strftime("%H:%M:%S")})
        self._trim()

    def remember_fact(self, key: str, value) -> None:
        """存一个会话事实，如 last_topic / last_report / last_hotlist_source。"""
        if value is not None:
            self.facts[key] = value

    def remember_list(self, items: list[str], label: str = "") -> None:
        """记录最近一次列表型输出（榜单条目/速递条目），供『第N条』类指代。"""
        if items:
            self.last_lists.append({"label": label, "items": items})
            if len(self.last_lists) > 3:
                self.last_lists.pop(0)

    def _trim(self) -> None:
        if len(self.history) > HISTORY_LIMIT * 2:
            self.history = self.history[-HISTORY_LIMIT * 2:]

    # ---- 读取 ----
    def last_user(self) -> str:
        return self.history[-1]["content"] if self.history else ""

    def recent_history(self, n: int = 6) -> list[dict]:
        """最近 n 条历史（不含最新用户消息时请自行切片）。"""
        return self.history[-n:]

    def context_blurb(self, n: int = 4) -> str:
        """把最近几轮历史压成一行上下文摘要，供 LLM 提示词注入。"""
        lines = []
        for h in self.history[-n * 2:]:
            who = "用户" if h["role"] == "user" else "助手"
            text = (h["content"] or "").replace("\n", " ")[:60]
            lines.append(f"{who}：{text}")
        return "\n".join(lines)

    def get_fact(self, key: str, default=None):
        return self.facts.get(key, default)

    # ---- 指代解析（规则层，零 LLM 成本）----
    REF_PATTERNS = [
        (re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*条"), "nth_item"),
        (re.compile(r"那个报告|这份报告|上次的报告|刚生成"), "last_report"),
        (re.compile(r"上次那个|刚才那个|上面那个|刚才的主题|这个话题|那个话题|刚才说|刚才问|上次聊"), "last_topic"),
        (re.compile(r"上次的|刚才的|上一条"), "last_reply"),
    ]

    def resolve_reference(self, text: str) -> str:
        """解析文本中的指代词。命中则返回补全后的描述，未命中原样返回。

        例：
            "第二条"            -> "（指最近列表中的第 2 条：「...」）"
            "那个报告在哪里"     -> "（指最近生成的报告：「weekly_ai_...md」）"
            "刚才那个主题"       -> "（指最近主题：「AI 热点」）"
        """
        resolved = text
        # 1) 第 N 条 → 最近列表条目
        m = self.REF_PATTERNS[0][0].search(text)
        if m:
            idx = self._cn_to_int(m.group(1))
            if idx and self.last_lists:
                last = self.last_lists[-1]
                if 1 <= idx <= len(last["items"]):
                    item = last["items"][idx - 1]
                    resolved = resolved.replace(
                        m.group(0),
                        f"「{last['label']}中第{idx}条：{item[:40]}」", 1,
                    )
                    return resolved
        # 2) 最近报告
        if self.REF_PATTERNS[1][0].search(text):
            report = self.facts.get("last_report")
            if report:
                resolved = f"{resolved}（指最近生成的报告：{report}）"
                return resolved
        # 3) 最近主题
        if self.REF_PATTERNS[2][0].search(text):
            topic = self.facts.get("last_topic")
            if topic:
                resolved = f"{resolved}（指最近的主题：「{topic}」）"
                return resolved
        # 4) 最近回复（兜底：补充上一条助手回复摘要）
        if self.REF_PATTERNS[3][0].search(text):
            prev = [h for h in reversed(self.history) if h["role"] == "agent"]
            if prev:
                resolved = f"{resolved}（指上一条回复，其内容摘要：{prev[0]['content'][:50]}）"
                return resolved
        return resolved

    @staticmethod
    def _cn_to_int(s: str) -> int:
        cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        if s.isdigit():
            return int(s)
        if s in cn:
            return cn[s]
        if s.endswith("十") and len(s) == 1:
            return 10
        return 0
