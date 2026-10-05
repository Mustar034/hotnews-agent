"""执行轨迹：把 Agent 的每一步（理解/计划/工具/反思/完成）记录成结构化轨迹。

可解释性的落点：用户随时可以看到 Agent 在做什么、用了什么工具、
结果如何、花费了多少成本——而不是黑盒输出。

用法：
    trace = Trace()
    trace.enter_task("research", title="AI 热点周报")
    trace.log("plan", "拆解为 3 个子问题")
    trace.log("tool", "search_bing", detail="关键词: 大模型 发布", status="ok", n=5)
    trace.render()   # 终端展示
"""
from datetime import datetime

from .config import TRACE_SHOWN


class Trace:
    def __init__(self):
        self.steps: list[dict] = []
        self.task_name = ""
        self.task_title = ""
        self._in_task = False

    # ---- 生命周期 ----
    def enter_task(self, name: str, title: str = "") -> None:
        self.task_name = name
        self.task_title = title
        self.steps = []
        self._in_task = True
        self.log("start", f"任务开始：{title or name}")

    def finish_task(self) -> None:
        if self._in_task:
            self.log("done", "任务完成")
            self._in_task = False

    # ---- 记录 ----
    def log(self, phase: str, action: str, detail: str = "",
            tool: str = "", status: str = "ok", n: int = 0) -> None:
        """记录一步。phase ∈ {understand, plan, tool, reflect, finish, start, done, note}。"""
        self.steps.append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "phase": phase,
            "action": action,
            "detail": detail,
            "tool": tool,
            "status": status,
            "n": n,
        })

    # ---- 展示 ----
    def render(self, show: bool = TRACE_SHOWN) -> None:
        """把轨迹渲染成终端可读文本。show=False 时静默（仍保留记录供摘要）。"""
        if not show:
            return
        for s in self.steps:
            tag = {
                "understand": "[理解]",
                "plan": "[计划]",
                "tool": "[工具]",
                "reflect": "[反思]",
                "finish": "[完成]",
                "start": "[任务]",
                "done": "[完成]",
                "note": "[说明]",
            }.get(s["phase"], "[步骤]")
            parts = [tag, s["action"]]
            if s["tool"]:
                parts.append(f"工具:{s['tool']}")
            if s["n"]:
                parts.append(f"×{s['n']}")
            if s["detail"]:
                parts.append(s["detail"])
            if s["status"] != "ok":
                parts.append(f"[{s['status']}]")
            print(" ".join(parts))

    # ---- 查询 ----
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s["phase"] == "tool")

    def failed_steps(self) -> list[dict]:
        return [s for s in self.steps if s["status"] != "ok"]

    def render_summary(self) -> str:
        """轨迹摘要：任务类型、工具调用次数、失败步骤、LLM 调用数。"""
        tools = {}
        for s in self.steps:
            if s["phase"] == "tool" and s["tool"]:
                tools[s["tool"]] = tools.get(s["tool"], 0) + 1
        tool_str = ", ".join(f"{k}×{v}" for k, v in tools.items()) or "无"
        fail_n = len(self.failed_steps())
        return (
            f"任务类型：{self.task_name or '无'}；工具调用：{tool_str}；"
            f"失败/降级步骤：{fail_n} 个。"
        )


# 全局共享实例
_trace = Trace()


def get_trace() -> Trace:
    return _trace
