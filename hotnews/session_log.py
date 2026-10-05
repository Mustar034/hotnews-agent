"""会话记录：把一次对话（启动→退出）完整保存为 markdown。

- 所有记录放在配置的根目录（默认 conversations/）下；
- 每个对话一个子文件夹，命名为「开始时间~结束时间」（如 2026-10-05_10-30-12~10-45-30）；
- 文件夹内对话记录.md 按轮次记录：用户输入 + Agent 输出（含计划/轨迹中间过程）；
- 退出时写入结束时间、成本统计，并把文件夹从「开始时间」重命名为「开始时间~结束时间」。

用法：
    slog = SessionLog()
    slog.attach()                       # 开始 tee 捕获 stdout
    slog.begin_round(n, "用户输入")
    ... 处理 ...
    slog.end_round()
    slog.detach()
    path = slog.finish(memory)          # 写统计 + 重命名，返回最终文件夹路径
"""
import os
import sys
from datetime import datetime

from .config import CONVERSATION_DIR


def _fmt(ts: datetime) -> str:
    return f"{ts:%Y-%m-%d_%H-%M-%S}"


def _ts() -> str:
    return f"{datetime.now():%H:%M:%S}"


class _Tee:
    """同时写入多个流的写代理：转发 stdout 到控制台与日志文件。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


class SessionLog:
    def __init__(self, root: str = CONVERSATION_DIR):
        self.start = datetime.now()
        self.root = root
        self.folder = os.path.join(root, _fmt(self.start))          # 先建「开始时间」文件夹
        os.makedirs(self.folder, exist_ok=True)
        self.md_path = os.path.join(self.folder, "对话记录.md")
        self._file = open(self.md_path, "a", encoding="utf-8")
        self._write_header()
        self._old_stdout = None
        self._round = 0

    # ---- 文件写入 ----
    def _write(self, text: str) -> None:
        self._file.write(text)
        self._file.flush()

    def _write_header(self) -> None:
        self._write(
            f"# 对话记录\n\n"
            f"- 开始时间：{self.start:%Y-%m-%d %H:%M:%S}\n"
            f"- 记录文件：{self.md_path}\n\n"
            f"---\n"
        )

    def attach(self) -> None:
        """开始捕获 stdout（Agent 的全部输出进入记录文件）。"""
        if self._old_stdout is None:
            self._old_stdout = sys.stdout
            sys.stdout = _Tee(self._old_stdout, self._file)

    def detach(self) -> None:
        """停止捕获，恢复原 stdout。"""
        if self._old_stdout is not None:
            sys.stdout = self._old_stdout
            self._old_stdout = None

    # ---- 轮次 ----
    def begin_round(self, n: int, user_text: str) -> None:
        self._round = n
        self._write(f"\n## 第 {n} 轮（{_ts()}）\n\n**用户**\n\n> {user_text}\n\n**Agent**\n\n```\n")

    def end_round(self) -> None:
        self._write("```\n")

    # ---- 结束 ----
    def finish(self, extra_note: str = "") -> str:
        """写入结束信息，把文件夹重命名为「开始时间~结束时间」，返回最终文件夹路径。"""
        end = datetime.now()
        self._write(
            f"\n---\n\n"
            f"- 结束时间：{end:%Y-%m-%d %H:%M:%S}\n"
            f"- 总轮次：{self._round}\n"
        )
        if extra_note:
            self._write(f"- 说明：{extra_note}\n")
        self._file.close()

        # 重命名：开始时间 → 开始时间~结束时间（失败则保持原名，不中断退出）
        final_folder = os.path.join(self.root, f"{_fmt(self.start)}~{_fmt(end)}")
        try:
            if final_folder != self.folder and not os.path.exists(final_folder):
                os.rename(self.folder, final_folder)
                self.folder = final_folder
                self.md_path = os.path.join(final_folder, "对话记录.md")
        except OSError:
            pass
        return self.folder
