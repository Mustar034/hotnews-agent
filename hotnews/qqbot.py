"""QQ 官方机器人接入：单聊对话 + 主动推送速递。

为什么是 QQ 官方开放平台（q.qq.com）：
- 个人即可注册开发者（实名认证），2 分钟创建机器人拿到 AppID/Secret，无需企业主体
- 收消息用 WebSocket 长连接，免公网回调地址（个人用户即可接入）
- 单聊主动消息额度宽（每个好友每天最多 1000 条），适合"每天 8 点推送速递"

注意（重要）：
- QQ 官方机器人【群聊主动消息每月仅 4 条】→ 本实现走"单聊"：加机器人好友，
  每天早上主动推送到私聊，平时直接对话
- 严禁个人号协议机器人（go-cqhttp / NapCat / Lagrange 等）：非官方、腾讯明令禁止、封号风险

用法：
    python -m hotnews.qqbot            # 启动 WebSocket 监听（单聊对话）
    python -m hotnews.brief --push     # 生成速递并推送到绑定 openid 的私聊

安全约定：凭据走环境变量/.env（QQ_APP_ID / QQ_APP_SECRET），代码零内置；
          未配置时功能自动禁用并给出明确提示，不影响终端/网页功能。
"""
import json
import os
import threading
import time
from typing import Optional

import requests

from . import config
from .engine import get_engine

_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_API_BASE = "https://api.sgroup.qq.com"
_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "qq_target_openid.json")


# ---------------------------------------------------------------------------
# openid 记录：用户在 QQ 里给机器人发过消息后自动绑定；brief 推送读取
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)


def save_target_openid(openid: str) -> None:
    """记录最新私聊用户的 openid（多用户时覆盖为最近一次，供速递推送）。"""
    if not openid:
        return
    _ensure_data_dir()
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"openid": openid, "ts": int(time.time())}, f, ensure_ascii=False)


def load_target_openid() -> str:
    """读取记录的单聊 openid；未绑定时回退 .env 的 QQ_TARGET_OPENID。"""
    if config.QQ_TARGET_OPENID:
        return config.QQ_TARGET_OPENID
    try:
        with open(_DATA_FILE, encoding="utf-8") as f:
            return json.load(f).get("openid", "")
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# OpenAPI 客户端：access_token 缓存 + 单聊消息发送（同步，供 brief/线程使用）
# ---------------------------------------------------------------------------

class QQClient:
    """QQ 机器人 OpenAPI 客户端。"""

    def __init__(self, app_id: str = "", app_secret: str = "") -> None:
        self.app_id = app_id or config.QQ_APP_ID
        self.app_secret = app_secret or config.QQ_APP_SECRET
        self._token: str = ""
        self._expire = 0.0
        self._seq = 0
        self._seq_lock = threading.Lock()

    def get_access_token(self, force: bool = False) -> str:
        """POST /app/getAppAccessToken 换取 access_token（2 小时有效，缓存 100 分钟）。"""
        now = time.time()
        if not force and self._token and now < self._expire:
            return self._token
        if not (self.app_id and self.app_secret):
            raise RuntimeError("未配置 QQ_APP_ID / QQ_APP_SECRET（.env 中设置 QQ 开放平台机器人凭据）")
        resp = requests.post(_TOKEN_URL, json={
            "appId": self.app_id, "clientSecret": self.app_secret,
        }, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"获取 QQ access_token 失败: {data}")
        self._token = data["access_token"]
        self._expire = now + int(data.get("expires_in", 7200)) - 600
        return self._token

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def send_private_message(self, openid: str, content: str,
                             msg_id: str = "") -> dict:
        """向单聊用户发送文本消息（v2/users/{openid}/messages）。

        - msg_id 非空 = 被动回复（用户消息 60 分钟内最多回复 4 次）
        - msg_id 为空 = 主动消息（必须带 msg_seq）
        """
        if not openid:
            raise RuntimeError("未绑定 QQ 单聊 openid：请先在 QQ 里给机器人发一条消息，"
                               "或设置 QQ_TARGET_OPENID")
        body = {"content": content, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        else:
            body["msg_seq"] = self._next_seq()
        resp = requests.post(
            f"{_API_BASE}/v2/users/{openid}/messages",
            headers={"Authorization": f"QQBot {self.get_access_token()}"},  # 官方 v2 API 前缀是 QQBot
            json=body,
            timeout=config.REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"QQ 发送失败 HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if "id" not in data:  # 正常返回含消息 id；错误码以非 0 形式给出
            raise RuntimeError(f"QQ 发送失败: {data}")
        return data

    # ---- 速递推送入口（brief 调用）----
    def push_brief(self, content: str) -> dict:
        openid = load_target_openid()
        if not openid:
            raise RuntimeError(
                "未配置 QQ 速递接收人：先在 QQ 里给机器人发一条消息（自动绑定 openid），"
                "或在 .env 设置 QQ_TARGET_OPENID")
        return self.send_private_message(openid, content)


# ---------------------------------------------------------------------------
# WebSocket 监听：单聊消息 → 后台线程跑 Agent → 回发（复用 engine 全链路）
# ---------------------------------------------------------------------------

class QQListener:
    """基于官方 SDK（qq-botpy）的 WebSocket 长连接监听。

    - intents 开启 public_messages（C2C 单聊消息）
    - 收到文本消息：记录 openid → 后台线程 engine.start_ask → OpenAPI 回发
    - 异步事件回调里只做投递，不阻塞事件循环（Agent 可能耗时数分钟）
    """

    def __init__(self, app_id: str = "", app_secret: str = "") -> None:
        self.app_id = app_id or config.QQ_APP_ID
        self.app_secret = app_secret or config.QQ_APP_SECRET

    def _handle_message(self, message) -> None:
        author = getattr(message, "author", None)
        # C2C 单聊消息的 openid 字段是 user_openid（注意：不是 author.openid）
        openid = getattr(author, "user_openid", "") or getattr(author, "openid", "")
        content = (getattr(message, "content", "") or "").strip()
        msg_id = getattr(message, "id", "") or ""
        if not openid or not content:
            return
        save_target_openid(openid)  # 自动绑定推送对象
        print(f"[qqbot] 收到单聊消息: openid=...{openid[-6:]} len={len(content)}")
        import sys
        sys.stdout.flush()

        def worker():
            client = QQClient(self.app_id, self.app_secret)
            try:
                engine = get_engine()
                sid = engine.create_session()
                qsink, thread = engine.start_ask(sid, content, interactive=False)
                reply_parts = []
                while True:
                    try:
                        ev = qsink.q.get(True, 0.3)
                    except Exception:
                        if not thread.is_alive() and qsink.q.empty():
                            break
                        continue
                    if ev is None:
                        break
                    if ev.type in ("agent", "digest") and ev.text:
                        reply_parts.append(ev.text)
                reply = "\n".join(reply_parts) or "处理完成，但没有生成可回复的内容。"
                client.send_private_message(openid, reply, msg_id=msg_id)
                print(f"[qqbot] 已回发: openid=...{openid[-6:]} len={len(reply)}")
                import sys
                sys.stdout.flush()
            except Exception as e:  # 兜底：错误也要回发，让用户知道
                print(f"[qqbot] 处理出错: {e}", flush=True)
                try:
                    client.send_private_message(openid, f"[HotNews] 处理出错：{e}", msg_id=msg_id)
                except Exception as e2:
                    print(f"[qqbot] 回发失败: {e2}", flush=True)

        threading.Thread(target=worker, daemon=True,
                         name=f"qq-{openid[-6:]}").start()

    def run(self) -> None:
        """阻塞运行 WebSocket 监听（Ctrl+C 退出）。"""
        if not (self.app_id and self.app_secret):
            raise RuntimeError("未配置 QQ_APP_ID / QQ_APP_SECRET（.env 中设置 QQ 开放平台机器人凭据）")
        import botpy
        from botpy import Intents

        class _Handler(botpy.Client):
            def __init__(self, listener, **kw):
                super().__init__(**kw)
                self._listener = listener

            async def on_c2c_message_create(self, message):  # 单聊（C2C）消息
                self._listener._handle_message(message)

            async def on_ready(self):
                print("[qqbot] 已连接 QQ 开放平台，等待私聊消息...")

            async def on_error(self, event, *args, **kwargs):
                print(f"[qqbot] 事件异常: {event}")

        handler = _Handler(self, intents=Intents(public_messages=True))
        handler.run(appid=self.app_id, secret=self.app_secret)


def configured_listener() -> Optional[QQListener]:
    """装配好的监听器；凭据缺失返回 None。"""
    if not (config.QQ_APP_ID and config.QQ_APP_SECRET):
        return None
    return QQListener()


def main() -> None:
    listener = configured_listener()
    if listener is None:
        raise SystemExit("未配置 QQ_APP_ID / QQ_APP_SECRET（.env 中设置 QQ 开放平台机器人凭据）")
    print("[qqbot] 启动 QQ 单聊监听（Ctrl+C 退出）")
    try:
        listener.run()
    except KeyboardInterrupt:
        print("\n[qqbot] 已停止")


if __name__ == "__main__":
    main()
