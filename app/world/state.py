"""世界状态的读写。

get_world_state 返回 dict 不返回 ORM 对象——ORM 对象挂着 session，关了会
DetachedInstanceError，也没法直接 JSON 序列化。

建玩家两个函数分工不同：get_or_create_player 按名字找、找不到才建（CLI 的
"旅行者"要跨会话复用同一条）；create_player 无条件新建（Web 每会话一份干净数据）。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.world.models import InventoryItem, Player, Quest, Relationship, get_engine


def get_world_state(player_id: int) -> dict:
    """组装某个玩家的世界状态快照。

    态度用 scalar_one_or_none（player_id unique，最多一行，可能还没有）；
    物品和任务多行，用 scalars().all()。
    """
    with Session(get_engine()) as session:
        relationship = session.execute(
            select(Relationship).where(Relationship.player_id == player_id)
        )
        rel = relationship.scalar_one_or_none()

        inventoryitem = session.execute(select(InventoryItem))
        inv = inventoryitem.scalars().all()

        quest = session.execute(select(Quest))
        que = quest.scalars().all()

    snapshot = {
        "player_id": player_id,
        "trust": rel.trust if rel else 0,
        "affection": rel.affection if rel else 0,
        "inventory": [
            {
                "item_key": i.item_key,
                "item_name": i.item_name,
                "holder": i.holder,
                "quantity": i.quantity,
            }
            for i in inv
        ],
        "quests": [
            {
                "quest_key": q.quest_key,
                "title": q.title,
                "stage": q.stage,
                "clues": q.clues,
            }
            for q in que
        ],
    }
    return snapshot


def get_or_create_player(name: str) -> int:
    """按名字找玩家，找不到就新建，返回 id。"""
    with Session(get_engine()) as session:
        player = session.execute(
            select(Player).where(Player.name == name)
        ).scalar_one_or_none()

        if player is not None:
            return player.id

        player = Player(name=name)
        session.add(player)
        session.commit()
        session.refresh(player)
        return player.id


def create_player(name: str) -> int:
    """无条件新建一个玩家，返回 id。

    name 没有唯一约束，重名不报错、但两个会话会互读历史，名字得由调用方保证不重
    （见 api.py 的 _new_visitor_name）。
    """
    with Session(get_engine()) as session:
        player = Player(name=name)
        session.add(player)
        session.commit()
        session.refresh(player)
        return player.id


# 下面三个是写工具的代码本体，Agent 通过它们真正改动世界状态。
def transfer_item(player_id:int,item_key:str,direction:str)->dict:
    """物品在 NPC 和玩家之间流转。

    direction 两值：give = NPC 给玩家（holder 写成 player:{id}），take = 玩家给 NPC。
    """
    with Session(get_engine()) as session:
        item=session.execute(
            select(InventoryItem).where(InventoryItem.item_key == item_key)
        ).scalar_one_or_none()
        if item is None:
            return {"ok": False, "error": "没有这件物品"}
        if direction == "give":
            item.holder = f"player:{player_id}"  # give：NPC 给玩家
        elif direction == "take":
            item.holder = "npc"  # take：玩家给 NPC
        else:
            return {"ok": False, "error": "direction 只能是 give 或 take"}
        session.commit()
        return {"ok": True, "item_key": item.item_key,
                "item_name": item.item_name, "holder": item.holder}
def update_quest(clue:str)->dict:
    """推进找阿福的任务。

    stage 上限 2，由玩家线索推进；stage 3（正式委托、给铜牌）由 NPC 自己决定。
    clues 是"玩家给过哪些线索"的账本，必须和 stage 分开记——stage 到顶后玩家再
    给线索仍要入账，两条绑在一个 if 里会让 stage 一到 2 就静默丢线索。
    同一条线索重复给不重复入账，也不重复推进 stage。
    """
    with Session(get_engine()) as session:
        quest=session.execute(
            select(Quest).where(Quest.quest_key == "find_apprentice")
        ).scalar_one_or_none()
        if quest is None:
            return {"ok": False, "error": "任务不存在"}
        if clue not in quest.clues:
            quest.clues = quest.clues + [clue]   # 必须拼出新列表再赋值，JSON 列就地 append 不被追踪
            if quest.stage < 2:
                quest.stage += 1
            session.commit()
        return {"ok": True, "stage": quest.stage, "title": quest.title, "clues": quest.clues}

def update_relationship(player_id: int, delta: int, reason: str) -> dict:
    """调整信任值。

    trust 加 delta 后钳制在 [-10, 10]。玩家可能还没有态度记录，这里懒建一行
    （default=0 只兜字段值、兜不住"行不存在"）。reason 供观测用，暂不落库。
    """
    with Session(get_engine()) as session:
        rel=session.execute(
            select(Relationship).where(Relationship.player_id == player_id)
        ).scalar_one_or_none()
        if rel is None:
            rel=Relationship(player_id=player_id,trust=0,affection=0)
            session.add(rel)
        new_trust=min(max(-10,rel.trust+delta),10)
        rel.trust=new_trust
        session.commit()
        return {"ok": True, "trust": rel.trust, "affection": rel.affection}