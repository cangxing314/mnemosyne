from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.agent.graph import build_graph
from collections import defaultdict
from uuid import uuid4

from app.world.state import create_player
from app.world.models import TurnLog, get_engine
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse

# session_id -> {"history": [...], "player_id": N}。只在进程内存里，重启即清空。
sessions: dict[str, dict] = {}


def _new_visitor_name() -> str:
    """给每个浏览器会话起一个不重名的访客名。

    不能用"访客 + 会话序号"：序号存在内存里，服务一重启就归零，新会话又叫
    "访客1"、撞上库里同名旧玩家，/history 会把别人的对话拉出来。
    """
    return f"访客-{uuid4().hex[:8]}"


app = FastAPI(title="老陈铁匠铺")
# 立绘等静态图片，前端按 /img/xxx.png 访问
app.mount("/img", StaticFiles(directory="static/img"), name="img")
graph = build_graph()               # POST /chat 用，带 checkpoint
graph_stream = build_graph(use_checkpoint=False)   # SSE 流式，SqliteSaver 不支持异步

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"  # 前端传的会话 id

@app.post("/chat")
def chat(req: ChatRequest):
    s = sessions.get(req.session_id)
    if s is None:
        s = sessions[req.session_id] = {
            "history": [],
            "player_id": create_player(_new_visitor_name()),
        }
    result = graph.invoke({
        "player_id": s["player_id"],
        "user_message": req.message,
        "turn": 0,
        "round_no": len(s["history"]) // 2,             # 一问一答两条，除以 2
        "ablation": {},
        "used_tools": [],                                     # 同 turn：checkpoint 存着上轮的，每轮清空
        "history": "\n".join(s["history"][-8:]),               # 和 CLI 同款，最近 8 条
    }, config={"configurable": {"thread_id": req.session_id}})
    reply = result.get("npc_reply", "")
    s["history"].append(f"玩家：{req.message}")                 # 聊完记进历史
    s["history"].append(f"老陈：{reply}")
    return {"reply": reply}

@app.get("/history")
def history(session_id: str = "default"):
    s=sessions.get(session_id)
    if s is None:
        return []
    with Session(get_engine()) as session:
        # 按 id 排不按 turn 排：turn 是会话内部的轮次，每个会话都从 0 开始，
        # 同一玩家有多段会话时号会重复，排序会互相穿插。id 是入库顺序。
        logs=session.query(TurnLog).filter(
            TurnLog.player_id == s["player_id"]
        ).order_by(TurnLog.id).all()
        result = []
        for log in logs:
            result.append({"who": "user", "text": log.user_message})
            result.append({"who": "npc", "text": log.npc_reply})
        return result

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    s = sessions.get(req.session_id)
    if s is None:
        s = sessions[req.session_id] = {
            "history": [],
            "player_id": create_player(_new_visitor_name()),
        }
    async def generator ():
        parts = []
        async for event in graph_stream.astream(
            {"player_id": s["player_id"], "user_message": req.message,"ablation": {},
             "turn": 0, "round_no": len(s["history"]) // 2, "used_tools": [],
             "history": "\n".join(s["history"][-8:])},
            stream_mode="custom",
        ):
            t = event.get("token", "") if isinstance(event, dict) else ""
            if t:
                parts.append(t)
                yield f"data: {t}\n\n"
        reply = "".join(parts)
        s["history"].append(f"玩家：{req.message}")
        s["history"].append(f"老陈：{reply}")
        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")

@app.get("/")
def index():
    return FileResponse("static/index.html")
