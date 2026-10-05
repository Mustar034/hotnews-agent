"""事件流：Agent 输出的统一结构化事件模型。

终端、网页（SSE）都通过 Event 消费 Agent 输出，实现"核心引擎与展示层解耦"：
- TerminalSink  打印到 stdout（终端模式，行为与改造前一致）
- BufferSink    收集到列表（测试/一次性拿全部事件）
- QueueSink     写入线程安全队列（FastAPI SSE 流式推送）

事件类型约定：
    plan       计划拆分展示（data.steps: [{step, tool, desc}]）
    agent      普通 Agent 回复（[Agent] ...）
    digest     速递正文（markdown 文本）
    reflection 反思纠错信息（[反思] ...）
    progress   任务进度（[任务]/[Worker]/[调度] ...）
    summary    会话摘要（[摘要] ...）
    error      错误提示
"""
from dataclasses import dataclass, field, asdict
import queue
from typing import Optional


@dataclass
class Event:
    type: str
    text: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class Sink:
    """输出接收器基类。"""

    def emit(self, event: Event) -> None:
        raise NotImplementedError


class TerminalSink(Sink):
    """终端渲染：与改造前行为一致（直接 print）。"""

    def emit(self, event: Event) -> None:
        print(event.text)


class BufferSink(Sink):
    """把事件收集进列表（测试 / 非流式场景）。"""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def drain(self) -> list[Event]:
        out, self.events = self.events, []
        return out


class QueueSink(Sink):
    """写入线程安全队列（跨线程 → SSE 推送）。"""

    def __init__(self, q: Optional[queue.Queue] = None) -> None:
        self.q = q or queue.Queue()

    def emit(self, event: Event) -> None:
        self.q.put(event)

    def close(self) -> None:
        self.q.put(None)  # sentinel：流结束


def say(sink: Optional[Sink], text: str, etype: str = "agent", **data) -> None:
    """统一输出入口：sink 为 None 时回退终端打印（保持旧行为）。"""
    (sink or TerminalSink()).emit(Event(etype, text, data))
