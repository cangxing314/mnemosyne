"""记忆的写入与查询。

session 由调用方传入，这样评测跑批能换到沙盒库上，同一套函数两处都用。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.memory.models import Memory


def add_memory(
    session: Session,
    *,
    user_id: str,
    kind: str,
    key: str,
    content_raw: str,
    importance: int = 5,
    source_turn: int = 0,
    entities: list[str] | None = None,
) -> Memory:
    """插入一条新记忆并返回。

    `*` 之后强制关键字传参——kind / key / content_raw 都是字符串，按位置传容易串位。
    version / is_active / superseded_by 由 supersede 内部维护，不收参数。
    """
    memory = Memory(
        user_id=user_id,
        kind=kind,
        key=key,
        content_raw=content_raw,
        importance=importance,
        source_turn=source_turn,
        entities=entities if entities is not None else [],
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory


def find_active(session: Session, *, user_id: str, key: str) -> Memory | None:
    """查某玩家某 key 的当前生效记忆，没有则 None。给 supersede 判断要不要沉底用。"""
    return session.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.key == key,
            Memory.is_active == True,  # noqa: E712
        )
    ).scalar_one_or_none()


def list_active(session: Session, user_id: str) -> list[Memory]:
    """查某玩家的全部生效记忆（面查）。主要给测试核对沉底结果用。"""
    return session.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.is_active == True,  # noqa: E712
        )
    ).scalars().all()


def supersede(session: Session, *, user_id: str, key: str,
              content_raw: str, kind: str = "episodic",
              importance: int = 5, source_turn: int = 0) -> Memory:
    """插入同 key 的新事实，旧那条自动沉底。

    新建时 version 取默认 1，只有发生覆盖才改成旧版 +1。
    """
    old = find_active(session, user_id=user_id, key=key)
    new = add_memory(session, user_id=user_id, kind=kind, key=key,
                     content_raw=content_raw, importance=importance,
                     source_turn=source_turn)
    if old is not None:
        old.is_active = False
        old.superseded_by = new.id
        new.version = old.version + 1
    session.commit()
    return new


