"""每日速递任务：生成最近 N 天（默认 3 天）的主题简报并推送到 QQ 单聊。

用法：
    python -m hotnews.brief                     # 最近3天 + AI 前沿 + 已配置 QQ 凭据则推送
    python -m hotnews.brief --date 2026-10-04   # 指定目标日（向前共 N 天）
    python -m hotnews.brief --days 5            # 窗口改最近 5 天
    python -m hotnews.brief --no-push           # 只生成不推送（本地预览）

跨天去重：同一新闻在相邻两天窗口重叠时只推一次（data/seen_brief.json 持久化）。

退出码：0 成功；1 生成或推送失败。
"""
import argparse
import datetime
import json
import os
import re
import sys

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


_AI_SUBTOPICS = [
    "头部 AI 公司新动态（OpenAI / DeepSeek / Anthropic / Google / Meta）",
    "新模型与大模型进展（发布、升级、开源、评测基准）",
    "AI 研究与突破（arXiv 论文、新方法、效率与推理优化）",
    "AI 硬件与算力（芯片、训练集群、推理成本下降）",
    "AI 应用与开发者生态（本地推理、工具链、API 更新）",
]

_SEEN_FILE = "data/seen_brief.json"   # 跨天去重状态（本地/云端各自维护，入库放行）
_SEEN_KEEP_DAYS = 14                  # seen 指纹最多保留 14 天，防止无限增长


def _norm_line(line: str) -> str:
    """速递条目规范化，供相似度去重（容忍 LLM 每天措辞漂移）。

    规则：去序号 → "日期不详"/具体日期归一为"月日" → 删评分/评论/HN评分数字噪音
    → 去点评括号 → 中英混用归一（Intelligent UI→智能UI）→ 保留模型版本号
    （Claude Haiku 5.5→claudehaiku55、GPT-6→gpt6，版本号是核心标识不可删）。
    """
    s = re.sub(r"^\s*\d+[\s\.\、]\s*", "", line)          # 去序号
    s = s.replace("日期不详", "月日")                       # 日期不详与具体日期占位对齐
    s = re.sub(r"\d{1,2}月\d{1,2}日", "月日", s)           # 具体日期归一（保留月日占位）
    s = re.sub(r"(?i)hn\s*评分\s*\d+", "", s)             # HN 评分/评论噪音（大小写都删）
    s = re.sub(r"评分\s*\d+", "", s)
    s = re.sub(r"评论\s*\d+", "", s)
    s = re.sub(r"[（(].*?[)）]", "", s)                     # 去点评/括号内容
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s).lower()      # 去符号，保留字母数字中文
    s = s.replace("intelligentui", "智能ui")               # 中英混用归一
    return s


def _sim(a: str, b: str) -> float:
    """两条规范化文本的 4-gram 相似度（0~1）。短文本直接比较。"""
    if not a or not b:
        return 0.0
    if len(a) <= 8 or len(b) <= 8:
        return 1.0 if a == b else 0.0
    ga = {a[i:i + 4] for i in range(len(a) - 3)}
    gb = {b[i:i + 4] for i in range(len(b) - 3)}
    inter = len(ga & gb)
    return inter / max(len(ga), len(gb), 1)


_SIM_THRESHOLD = 0.42  # 相似度超过该值判定为同一条新闻（容忍措辞/来源漂移）


def _load_seen() -> list[dict]:
    try:
        with open(_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_seen(seen: list[dict]) -> None:
    os.makedirs(os.path.dirname(_SEEN_FILE), exist_ok=True)
    with open(_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)


def _filter_date_range(content: str, start: datetime.date, end: datetime.date) -> str:
    """确定性窗口过滤：条目带日期且不在 [start, end] 内（含未来日期）则剔除。

    无日期/日期不详的条目保留（多日窗口宽松策略）。
    """
    kept = []
    for ln in content.splitlines():
        m = re.search(r"(\d{1,2})月(\d{1,2})日", ln)
        if m:
            try:
                d = datetime.date(end.year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                kept.append(ln)
                continue
            if d < start or d > end:
                continue
        kept.append(ln)
    return "\n".join(kept)


def dedupe_brief(content: str, today: str, start: datetime.date,
                 end: datetime.date) -> tuple[str, list[str]]:
    """跨天去重 + 窗口过滤：剔除超出日期范围的条目与已推送过的重复条目。

    判定：新条目规范化后与 seen 历史逐条算 4-gram 相似度，> _SIM_THRESHOLD 判重复。
    新的入 seen 并写回；若全部重复，保留最近 1 条并更新 seen（避免全空）。
    seen 只保留 _SEEN_KEEP_DAYS 天内的记录，防止无限增长。
    """
    content = _filter_date_range(content, start, end)
    seen = _load_seen()
    lines = [ln for ln in content.splitlines() if re.match(r"^\s*\d+[\s\.\、]", ln)]
    if not lines:
        # 无编号行（含"未获取到可展示的速递条目"等空壳）：视为无内容，不推送
        return "", []

    # 内部去重：同一速递内相似条目只留第一条（防 LLM 编辑重复输出）
    inner: list[str] = []
    for ln in lines:
        norm = _norm_line(ln)
        if any(_sim(norm, _norm_line(d)) > _SIM_THRESHOLD for d in inner):
            continue
        inner.append(ln)
    lines = inner

    kept, new_fps = [], []
    for ln in lines:
        norm = _norm_line(ln)
        if any(_sim(norm, s.get("norm", "")) > _SIM_THRESHOLD for s in seen):
            continue
        kept.append(ln)
        new_fps.append({"norm": norm, "date": today})

    if not kept and lines:
        # 全部与历史重复：无新内容可推。返回空串，调用方跳过推送（不硬塞重复条目）。
        return "", []

    seen = seen + new_fps
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=_SEEN_KEEP_DAYS)).isoformat()
    seen = [s for s in seen if s.get("date", today) >= cutoff][-200:]
    _save_seen(seen)

    if not kept:
        return "", []
    # 重新编号（LLM 编辑可能输出 1/2/5 跳号，统一为连续 1..N）
    kept = [re.sub(r"^\s*\d+[\s\.\、]\s*", f"{i + 1} ", ln) for i, ln in enumerate(kept)]
    # 总结行条数校正（LLM 编辑可能写"这5条"但实际 N 条）
    content = re.sub(r"这\d+条", f"这{len(kept)}条", content)
    body = content.splitlines()
    # 用 kept 按序替换编号行；未进 kept 的编号行（重复/超窗）直接删除
    idx = 0
    new_body = []
    for ln in body:
        if re.match(r"^\s*\d+[\s\.\、]", ln):
            if idx < len(kept):
                new_body.append(kept[idx])
                idx += 1
            # 多余的编号行（重复条目）删除
        else:
            new_body.append(ln)
    return "\n".join(new_body), [s["norm"] for s in new_fps]


def run_brief(date_spec: str = "yesterday", theme: str = "ai",
              days: int = 3, push: bool = False, timeout: float = 480.0) -> str:
    """生成速递正文（digest），可选推送。返回 digest 文本。

    窗口：以目标日（默认昨天）为终点向前共 days 天（如 days=3 → 昨天+前天+大前天）。
    跨天去重：同一新闻在相邻两天的 3 天窗口里重现时只推一次（seen 持久化）。
    """
    iso = resolve_date(date_spec)
    end = datetime.date.fromisoformat(iso)
    start = end - datetime.timedelta(days=days - 1)
    # 直接构造场景（不走 LLM 意图解析）：窗口完全由目标日决定，稳定可控
    from . import research
    if theme in ("ai", "AI", "人工智能"):
        intent = {
            "topic": "AI 前沿",
            "topic_slug": "ai",
            "topic_en": "artificial intelligence",
            "title": "AI前沿速递",
            "time_window": f"近{days}天",
            "time_slug": f"last{days}d",
            "date_range": (start, end),
            "subtopics": _AI_SUBTOPICS,
        }
    else:
        intent = {
            "topic": theme,
            "topic_slug": "tech",
            "topic_en": "",
            "title": f"{theme}速递",
            "time_window": f"近{days}天",
            "time_slug": f"last{days}d",
            "date_range": (start, end),
        }
    scenario = research.build_scenario(intent)
    result = research.run_research(scenario)

    content = result["digest"]
    content = fix_digest_header(content, iso)  # 标题日期显示目标日期而非生成时刻
    # 跨天去重 + 窗口日期过滤（Actions/本地各自维护 seen 文件，互不干扰）
    content, _ = dedupe_brief(content, iso, start, end)
    if not content.strip():
        print("[brief] 今日无新内容（全部与已推送历史重复），跳过推送。")
        return ""
    if push:
        results = QQClient().push_brief(content)  # list[dict]，每个接收人一条
        for r in results:
            if "error" in r:
                print(f"[brief] 推送失败: openid={r.get('openid')} error={r.get('error')}")
            else:
                print(f"[brief] 已推送: msg_id={r.get('id')}")
    else:
        print(f"[brief] 未推送（--push 才会推送；已生成如下速递）：\n\n{content}")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="HotNews 每日速递")
    parser.add_argument("--date", default="yesterday",
                        help="目标日期：yesterday / today / YYYY-MM-DD（默认 yesterday）")
    parser.add_argument("--theme", default="ai", help="调研主题方向（默认 ai）")
    parser.add_argument("--days", type=int, default=3,
                        help="时间窗口天数（以目标日为终点向前 N 天，默认 3）")
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
        run_brief(date_spec=args.date, theme=args.theme, days=args.days, push=push_flag)
    except (RuntimeError, TimeoutError, ValueError) as e:
        print(f"[brief] 失败：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
