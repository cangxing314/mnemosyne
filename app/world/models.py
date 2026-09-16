"""世界状态数据模型：players / relationships / inventory / quests / turn_log。

一个玩家只允许一条态度记录（player_id unique），态度变化走 UPDATE 不走 INSERT。
用同步版 SQLAlchemy——LangGraph 节点是同步函数，这个量级下同步更省事。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """表模型基类，继承它的类会自动登记进 metadata，供 create_all 建表。"""


class Player(Base):
    """一个玩家，对应一个会话线程（thread_id）。"""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Relationship(Base):
    """NPC 对某个玩家的态度。两个改动来源：规则引擎（确定、可复现）和
    update_relationship 工具（Agent 主动表态）。"""

    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), unique=True
    )
    trust: Mapped[int] = mapped_column(Integer, default=0)
    affection: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class InventoryItem(Base):
    """一件物品，以及它当前在谁手里。"""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # id 是锚；item_key 给代码引用，文案变了它不动；item_name 是显示文案
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    item_name: Mapped[str] = mapped_column(String(64), nullable=False)
    holder: Mapped[str] = mapped_column(String(64), nullable=False, default="npc")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class Quest(Base):
    """NPC 的目标任务。stage 是状态机（0/1/2），clues 是追加式 JSON 数组。"""

    __tablename__ = "quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quest_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clues: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class TurnLog(Base):
    __tablename__ = "turn_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"))
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message: Mapped[str] = mapped_column(String(1024), nullable=False)
    npc_reply: Mapped[str] = mapped_column(String(1024), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=True)
    facts: Mapped[list[dict]] = mapped_column(JSON, default=list)
    trust_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=True)
    # 成本与延迟，跨模型对比要用
    model: Mapped[str] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# 沙盒评测靠改这个环境变量，指向每场景自己的库文件
import os
DB_PATH = os.getenv("MNEMOSYNE_DB", "data/world.db")


def get_engine():
    """拿数据库引擎，表不存在会自动建（幂等）。"""
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Base.metadata.create_all(engine)
    return engine
