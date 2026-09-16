"""种子数据：往空库里放一局开局状态。

1 个演示玩家、1 条初始态度、10 件物品（都在 NPC 手里）、1 个任务。
session 由调用方传入，评测跑批才能换到沙盒库上。
"""

from sqlalchemy.orm import Session
from sqlalchemy import delete
from app.world.models import InventoryItem, Player, Quest, Relationship, get_engine


def seed(session: Session) -> None:
    # 清空旧数据让 seed 可重复执行，先子表后父表
    session.execute(delete(Relationship))  # 子表，引用 players
    session.execute(delete(InventoryItem))
    session.execute(delete(Quest))
    session.execute(delete(Player))  # 父表最后删

    # 演示玩家
    player = Player(name="旅行者")
    session.add(player)
    session.flush()  # 立刻执行 INSERT，把自增 id 拿到手

    # 初始态度，全 0
    session.add(Relationship(player_id=player.id, trust=0, affection=0))

    # 10 件物品，全在 NPC 手里。
    # iron_dagger 的 key 不能改（C1 断言依赖它）；apprentice_pendant 是 stage 2 后要转交的。
    session.add_all(
        [
            InventoryItem(item_key="iron_dagger", item_name="铁匕首", holder="npc"),
            InventoryItem(item_key="healing_herb", item_name="止血草药", holder="npc"),
            InventoryItem(item_key="lucky_charm", item_name="幸运符", holder="npc"),
            InventoryItem(item_key="harvest_sickle", item_name="新打好的镰刀", holder="npc"),
            InventoryItem(item_key="whetstone", item_name="磨刀石", holder="npc"),
            InventoryItem(item_key="iron_hoe", item_name="铁锄头", holder="npc"),
            InventoryItem(item_key="nine_fold_steel", item_name="附魔刀坯", holder="npc"),
            InventoryItem(item_key="old_bellows", item_name="旧风箱", holder="npc"),
            InventoryItem(item_key="apprentice_pendant", item_name="学徒铜牌", holder="npc"),
            InventoryItem(item_key="mountain_ore", item_name="灰烬魔铁矿", holder="npc"),
        ]
    )

    # 任务，stage=0 表示还没有线索
    session.add(
        Quest(quest_key="find_apprentice", title="寻找阿福", stage=0, clues=[])
    )

    session.commit()
    print("种子数据写入完成：1 玩家 / 10 物品 / 1 任务")


def main() -> None:
    with Session(get_engine()) as session:
        seed(session)


if __name__ == "__main__":
    main()
