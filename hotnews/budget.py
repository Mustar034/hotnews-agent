"""成本预算：LLM 调用计数 + 任务/会话两级上限 + 估算 token/费用统计。

设计原则（成本可控）：
- 会话级上限（MAX_LLM_CALLS_PER_SESSION）：防止长会话累计失控；
- 任务级上限（MAX_LLM_CALLS_PER_TASK）：每个任务独立记账，超限即收敛；
- 所有 LLM 调用必须经 budget.spend() 记账，超限抛 BudgetExceeded；
- 提供估算 token/费用输出（明确标注为估算值，便于用户感知成本）。

用法：
    budget = Budget()
    budget.enter_task("research")
    try:
        budget.spend()          # 每次 LLM 调用前调用
        ...
    finally:
        budget.exit_task()
    budget.summary()            # 会话结束输出统计
"""
import time
from datetime import datetime

from .config import MAX_LLM_CALLS_PER_SESSION, MAX_LLM_CALLS_PER_TASK


class BudgetExceeded(Exception):
    """预算耗尽信号：调用方应收敛任务，输出部分结果并说明原因。"""


class Budget:
    def __init__(self, session_limit: int = MAX_LLM_CALLS_PER_SESSION,
                 task_limit: int = MAX_LLM_CALLS_PER_TASK):
        self.session_limit = session_limit
        self.task_limit = task_limit
        self.session_calls = 0
        self.task_calls = 0
        self._current_task = None
        self._task_start = None
        self._log: list[dict] = []   # 每次调用的记账明细

    # ---- 记账 ----
    def enter_task(self, name: str) -> None:
        """进入新任务：重置任务级计数。"""
        self._current_task = name
        self.task_calls = 0
        self._task_start = time.time()

    def exit_task(self) -> None:
        """离开当前任务。"""
        self._current_task = None
        self.task_calls = 0

    def spend(self, est_chars: int = 0, est_tokens: int = 0) -> None:
        """每次 LLM 调用前调用。超出任一上限即抛 BudgetExceeded。"""
        if self.session_calls >= self.session_limit:
            raise BudgetExceeded(
                f"本次会话的 LLM 调用已达上限（{self.session_limit} 次），为控制成本已停止新的智能调用。"
                "您可以结束对话重新开始，或调大 config.MAX_LLM_CALLS_PER_SESSION。"
            )
        if self._current_task and self.task_calls >= self.task_limit:
            raise BudgetExceeded(
                f"当前任务「{self._current_task}」的 LLM 调用已达上限（{self.task_limit} 次），已按部分结果收敛。"
            )
        self.session_calls += 1
        if self._current_task:
            self.task_calls += 1
        self._log.append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "task": self._current_task or "chat",
            "est_chars": est_chars,
            "est_tokens": est_tokens or max(1, est_chars // 2),  # 中文约 1 字 ≈ 0.5~1 token，取保守值
        })

    # ---- 查询 ----
    @property
    def calls(self) -> int:
        return self.session_calls

    @property
    def remaining(self) -> int:
        return max(0, self.session_limit - self.session_calls)

    @property
    def in_task(self) -> bool:
        return self._current_task is not None

    # ---- 统计输出 ----
    def estimated_tokens(self) -> int:
        """估算累计输入输出 token（粗估，仅用于用户感知成本）。"""
        return sum(item["est_tokens"] for item in self._log)

    def summary(self) -> str:
        """会话成本摘要：调用次数 + 估算 token（明确标注估算）。"""
        est_tok = self.estimated_tokens()
        # DeepSeek-chat 约 ¥1-2/百万 token（输入/输出混合粗估），给数量级即可
        est_cost = est_tok / 1_000_000 * 2
        return (
            f"本次会话 LLM 调用 {self.session_calls} 次（上限 {self.session_limit}），"
            f"估算 token 约 {est_tok:,}（粗估），估算费用约 ¥{est_cost:.3f}（仅供参考）。"
            "确定性能力（天气/日期/榜单/规则）不消耗 LLM。"
        )


# 全局共享实例（模块级单例，进程内唯一）
_budget = Budget()


def get_budget() -> Budget:
    return _budget
