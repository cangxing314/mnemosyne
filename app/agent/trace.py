"""运行痕迹：把每轮对话写进 turn_log，供评测回放过程。只记录，不影响业务逻辑。"""

from sqlalchemy.orm import Session

from app.world.models import TurnLog, get_engine


def log_turn(player_id: int, turn: int, user_message: str, npc_reply: str,
             tool_name: str | None = None, facts: list = [],
             trust_delta: int = 0, reason: str | None = None,
             model: str | None = None, latency_ms: int | None = None,
             tokens: int | None = None) -> None:
    """写一行 turn_log，只追加不查不改。

    参数都有默认值，没调工具或信任没变时可以不传；
    model / latency_ms / tokens 给评测报告和跨模型 sweep 取数用。
    """
    with Session(get_engine()) as session:
        session.add(TurnLog(player_id=player_id, turn=turn,
                            user_message=user_message, npc_reply=npc_reply,
                            tool_name=tool_name, facts=facts,
                            trust_delta=trust_delta, reason=reason,
                            model=model, latency_ms=latency_ms, tokens=tokens))
        session.commit()
