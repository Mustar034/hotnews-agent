"""引擎层：会话管理 + 事件流入口（网页/微信共用的无界面入口）。

- AgentEngine 维护多个会话（session_id → SessionState），每会话独立
  memory/budget/trace/sink（contextvars 隔离），同一进程多会话并发互不干扰
- start_ask() 在后台线程执行一轮任务，事件经 QueueSink 流出（供 SSE 流式推送），
  同时 BufferSink 留档到会话历史（刷新页面可恢复）
- interactive=False：跳过"是否生成完整报告"等终端交互，直接生成（网页/微信入口）

用法（FastAPI SSE）：
    engine = AgentEngine()
    sid = engine.create_session()
    qsink, thread = engine.start_ask(sid, "今天天气")
    while True:
        ev = await asyncio.to_thread(qsink.q.get, True, 0.5)
        if ev is None: break          # sentinel
        yield f"data: {json.dumps(ev.to_dict())}\n\n"
"""
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .agent_core import handle_task
from .budget import Budget
from .context import AgentContext, reset_context, set_context
from .events import BufferSink, Event, QueueSink, Sink, say
from .memory import SessionMemory
from .trace import Trace


class TeeSink(Sink):
    """同时写入多个 sink（历史留档 + 流式推送）。"""

    def __init__(self, *sinks):
        self.sinks = sinks

    def emit(self, event: Event) -> None:
        for s in self.sinks:
            s.emit(event)


@dataclass
class SessionState:
    id: str
    created: float = field(default_factory=time.time)
    memory: SessionMemory = field(default_factory=SessionMemory)
    messages: list = field(default_factory=list)  # [{"role","text","events":[...]}]
    last_report: str = ""


class AgentEngine:
    """多会话 Agent 引擎。"""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    # ---- 会话管理 ----
    def create_session(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._lock:
            self._sessions[sid] = SessionState(id=sid)
        return sid

    def get_session(self, sid: str) -> Optional[SessionState]:
        return self._sessions.get(sid)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [
                {"id": s.id, "created": s.created,
                 "messages": len(s.messages), "last_report": s.last_report}
                for s in self._sessions.values()
            ]

    def session_messages(self, sid: str) -> list[dict]:
        s = self._sessions.get(sid)
        return s.messages if s else []

    # ---- 执行 ----
    def start_ask(self, sid: str, user_input: str,
                  interactive: bool = False) -> tuple[QueueSink, threading.Thread]:
        """后台线程执行一轮任务；事件写入返回的 QueueSink（None 为结束哨兵）。

        同一会话串行：若已有任务在跑，直接拒绝并发出提示事件（避免并发错乱）。
        """
        state = self._sessions[sid]
        qsink = QueueSink()
        buf = BufferSink()
        tee = TeeSink(buf, qsink)
        thread = threading.Thread(
            target=self._ask_inner,
            args=(state, user_input, tee, buf, qsink, interactive),
            daemon=True,
            name=f"ask-{sid[:6]}",
        )
        thread.start()
        return qsink, thread

    def _ask_inner(self, state: SessionState, user_input: str, sink: Sink,
                   buf: BufferSink, qsink: QueueSink, interactive: bool) -> None:
        """会话上下文内执行一轮任务，结束后写历史与哨兵。"""
        ctx = AgentContext(memory=state.memory, sink=sink,
                           budget=Budget(), trace=Trace())
        token = set_context(ctx)
        try:
            handle_task(user_input, state.memory, sink=sink, interactive=interactive)
        except Exception as e:  # 兜底：绝不让一轮异常弄死整个会话
            say(sink, f"[Agent] 内部错误：{e}", "error")
        finally:
            reset_context(token)
            state.messages.append({
                "role": "user",
                "text": user_input,
                "events": [e.to_dict() for e in buf.drain()],
            })
            report = state.memory.get_fact("last_report")
            if report:
                state.last_report = report
            qsink.close()  # sentinel：通知 SSE 流结束


# 模块级单例：全进程共享一个引擎（FastAPI 单进程部署即可）
_engine: Optional[AgentEngine] = None


def get_engine() -> AgentEngine:
    global _engine
    if _engine is None:
        _engine = AgentEngine()
    return _engine
