"""长期记忆的事实层。

版本沉底：同 key 的新记忆写入时旧那条 is_active 置 False，检索只出生效版本。
key 触发覆盖、is_active 当开关、superseded_by 串版本链。跟 RAG 的分界就在这。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

# 复用 world 的 Base 与 get_engine：世界状态 + 记忆同库（data/world.db），一张库全包
from app.world.models import Base


class Memory(Base):
    """一条关于玩家的长期记忆，存原文不存摘要。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # episodic / semantic / preference / relationship / summary
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 归一化主题键，同 key 的新记忆会触发旧记忆沉底
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    # 事实层保原文不压缩；kind="summary" 的摘要正文也放这
    content_raw: Mapped[str] = mapped_column(Text, nullable=False)
    entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    importance: Mapped[int] = mapped_column(Integer, default=5)
    source_turn: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
