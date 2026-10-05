"""每日速递任务：生成指定日期的主题简报并推送到 QQ 单聊。

用法：
    python -m hotnews.brief                     # 昨天 + AI 方向 + 已配置 QQ 凭据则推送
    python -m hotnews.brief --date 2026-10-04   # 指定日期
    python -m hotnews.brief --date yesterday --theme ai --push
    python -m hotnews.brief --no-push           # 只生成不推送（本地预览）

依赖：复用现有 research/engine 全链路（拆解→并行调研→汇总→反思），
      从事件流中提取 digest（速递正文）推送，输出与网页/终端同源。

退出码：0 成功；1 生成或推送失败。
"""
import argparse
import datetime
import queue
import re
import sys
import time

from .engine import get_engine
from .qqbot import QQClient


def resolve_date(spec: str) -> str:
    """--date 参数 → YYYY-MM-DD（today / yesterday / 显式日期）。"""
    today = datetime.date.today()
    if spec in ("", "yesterday", "昨天"):
        return (today - datetime.timedelta(days=1)).isoformat()
    if spec in ("today", "今天"):
        return today.isoformat()
    d = datetime.date.fromisoformat(spec)  # 非法格式直接抛错
    return d.isoformat()


def human_date(iso: str) -> str:
    """YYYY-MM-DD → 2026年10月04日（用于 prompt 与标题）。"""
    d = datetime.date.fromisoformat(iso)
    return f"{d.year}年{d.month}月{d.day}日"


def fix_digest_header(content: str, iso: str) -> str:
    """digest 第一行标题默认带生成时刻日期（如"10-05（周一）"），
    定时速递场景应显示目标日期（昨天）。仅改写首行标题，正文不动。"""
    d = datetime.date.fromisoformat(iso)
    weekdays = "一二三四五六日"
    return re.sub(
        r"\d{2}-\d{2}（周[一二三四五六日]）",
        f"{d.month:02d}-{d.day:02d}（周{weekdays[d.weekday()]}）",
        content,
        count=1,
    )


def run_brief(date_spec: str = "yesterday", theme: str = "ai",
              push: bool = False, timeout: float = 420.0) -> str:
    """生成速递正文（digest），可选推送。返回 digest 文本。"""
    iso = resolve_date(date_spec)
    # 消息带"昨天"关键词：normalize_window 才能识别时间窗口（报告文件名/时间范围/搜索约束
    # 都用目标日期，而不是生成时刻的"今天"）
    engine = get_engine()
    sid = engine.create_session()
    msg = f"昨天（{human_date(iso)}）{theme} 方向有哪些值得关注的科技新闻？"
    qsink, thread = engine.start_ask(sid, msg, interactive=False)

    digest_parts: list[str] = []
    deadline = time.time() + timeout
    while True:
        if time.time() > deadline:
            raise TimeoutError("生成速递超时（请检查网络与 LLM 可用性）")
        try:
            ev = qsink.q.get(True, 0.5)
        except queue.Empty:
            if not thread.is_alive() and qsink.q.empty():
                break
            continue
        if ev is None:
            break
        if ev.type == "digest" and ev.text:
            digest_parts.append(ev.text)
        elif ev.type == "error":
            raise RuntimeError(f"生成失败：{ev.text}")

    if not digest_parts:
        raise RuntimeError("未生成速递内容（digest 事件为空，请查看完整事件流定位原因）")

    content = "\n".join(digest_parts)
    content = fix_digest_header(content, iso)  # 标题日期显示目标日期而非生成时刻
    if push:
        result = QQClient().push_brief(content)
        print(f"[brief] 已推送到 QQ 单聊: msg_id={result.get('id')}")
    else:
        print(f"[brief] 未推送（--push 才会推送；已生成如下速递）：\n\n{content}")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="HotNews 每日速递")
    parser.add_argument("--date", default="yesterday",
                        help="目标日期：yesterday / today / YYYY-MM-DD（默认 yesterday）")
    parser.add_argument("--theme", default="ai", help="调研主题方向（默认 ai）")
    parser.add_argument("--push", dest="push", action="store_true", default=None,
                        help="推送到 QQ 单聊（未指定时：配置了 QQ 凭据才推送）")
    parser.add_argument("--no-push", dest="push", action="store_false",
                        help="禁止推送，只打印速递正文")
    args = parser.parse_args()

    push_flag = args.push
    if push_flag is None:  # 未显式指定 → 配置了 QQ 凭据才推送
        from . import config
        push_flag = bool(config.QQ_APP_ID and config.QQ_APP_SECRET)

    try:
        run_brief(date_spec=args.date, theme=args.theme, push=push_flag)
    except (RuntimeError, TimeoutError, ValueError) as e:
        print(f"[brief] 失败：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
