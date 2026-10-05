"""会话上下文：让 budget/trace/memory/sink 在「会话」维度隔离。

- 终端模式：不设置上下文 → get_budget()/get_trace() 回退模块级单例（行为完全不变）
- Web 模式：engine.ask 为该会话设置独立 Context（budget/trace/memory/sink），
  同一进程内多会话并发互不干扰
- ThreadPoolExecutor 的 Worker 线程：通过 copy_context().run() 传播会话上下文
  （research.run_research 提交任务时捕获），保证预算/轨迹记到正确会话

用法：
    ctx = AgentContext(memory=state.memory, sink=sink)
    token = set_context(ctx)
    try:
        ...  # 该会话内 get_budget()/get_trace() 都指向 ctx 的实例
    finally:
        reset_context(token)
"""
import contextvars
from dataclasses import dataclass, field
from typing import Optional

from .events import Sink


@dataclass
class AgentContext:
    memory: object = field(default=None)      # SessionMemory（由引擎持有，此处引用）
    sink: Optional[Sink] = None               # 事件输出接收器
    budget: object = field(default=None)      # Budget 实例；None 时回退模块单例
    trace: object = field(default=None)       # Trace 实例；None 时回退模块单例


_ctx_var: contextvars.ContextVar = contextvars.ContextVar("hotnews_ctx", default=None)


def set_context(ctx: AgentContext):
    return _ctx_var.set(ctx)


def reset_context(token) -> None:
    _ctx_var.reset(token)


def get_context() -> Optional[AgentContext]:
    return _ctx_var.get()
