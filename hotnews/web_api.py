"""FastAPI 后端：SSE 流式对话 + 会话管理 + 静态托管前端。

运行（开发）：
    python -m hotnews.web_api            # 启动于 http://127.0.0.1:8000
    # 或 uvicorn hotnews.web_api:app --port 8000 --reload

接口：
    POST /api/chat         {"session_id": "", "message": "..."} → SSE 事件流
    GET  /api/sessions                   会话列表
    GET  /api/sessions/{sid}/messages    会话历史（刷新恢复）
    GET  /api/reports                    已生成的报告文件列表
    GET  /                               前端页面（web/dist，构建后）

安全：API Key 只在后端环境变量/.env，前端零暴露；会话预算沿用 budget。
"""
import asyncio
import json
import os
import queue

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .engine import get_engine

app = FastAPI(title="HotNews Web", docs_url="/api/docs", openapi_url="/api/openapi.json")

# 前端构建产物目录（web/dist）；存在时才挂载静态托管
_DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web", "dist"))
_ASSETS = os.path.join(_DIST, "assets")


class ChatRequest:
    """轻量请求体（避免 pydantic 强依赖校验复杂度）。"""

    def __init__(self, session_id: str = "", message: str = ""):
        self.session_id = session_id
        self.message = message


def _parse_chat_request(body: dict) -> ChatRequest:
    return ChatRequest(
        session_id=str(body.get("session_id", "") or ""),
        message=str(body.get("message", "") or "").strip(),
    )


@app.get("/api/sessions")
def list_sessions():
    return {"sessions": get_engine().list_sessions()}


@app.post("/api/chat")
async def chat(body: dict):
    req = _parse_chat_request(body)
    if not req.message:
        raise HTTPException(400, "消息不能为空")
    engine = get_engine()
    if req.session_id:
        if not engine.get_session(req.session_id):
            raise HTTPException(404, "会话不存在")
        sid = req.session_id
    else:
        sid = engine.create_session()

    qsink, thread = engine.start_ask(sid, req.message, interactive=False)

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.to_thread(qsink.q.get, True, 0.3)
                except queue.Empty:
                    # 队列超时：任务还在跑，继续轮询；线程结束且队列空 → 收尾
                    if not thread.is_alive() and qsink.q.empty():
                        break
                    continue
                if ev is None:
                    break  # 哨兵：任务结束
                yield f"data: {json.dumps(ev.to_dict(), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'text': '', 'data': {'session_id': sid}}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            # 客户端断开：后台任务（daemon 线程）继续跑完，事件入队后自然终止
            raise

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sessions/{sid}/messages")
def session_messages(sid: str):
    engine = get_engine()
    if not engine.get_session(sid):
        raise HTTPException(404, "会话不存在")
    return {"session_id": sid, "messages": engine.session_messages(sid)}


@app.get("/api/reports")
def list_reports():
    """列出项目根目录已生成的调研/榜单报告（*.md，排除 README 与会话记录）。"""
    files = []
    for name in sorted(os.listdir(os.getcwd()), reverse=True):
        if name.endswith(".md") and not name.startswith("README"):
            files.append({"name": name, "url": f"/reports/{name}"})
    return {"reports": files}


@app.get("/reports/{name}")
def serve_report(name: str):
    """下载/查看报告文件（防路径穿越）。"""
    if "/" in name or "\\" in name or not name.endswith(".md"):
        raise HTTPException(400, "非法文件名")
    path = os.path.join(os.getcwd(), name)
    if not os.path.isfile(path):
        raise HTTPException(404, "报告不存在")
    return FileResponse(path, media_type="text/markdown", filename=name)


# ---- 静态托管前端（构建产物存在时）----
if os.path.isdir(_ASSETS):
    app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_DIST, "index.html"))
else:
    @app.get("/")
    def index():
        return {"msg": "HotNews Web API 已就绪；前端未构建（web/dist 不存在）。"
                       "请在 web/ 目录执行 npm install && npm run build 后刷新本页。"}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
