"""工具集：6 个 LLM 可调用的工具（3 读 + 3 写）。

make_tools(player_id) 按玩家生成一套工具，player_id 被闭包捕获——LLM 看不到它，
每个玩家的工具只操作自己的数据，天然多用户隔离。

@tool 装饰器把函数名 / docstring / 参数转成"工具说明书"发给 LLM，docstring 就是
说明书正文。底层函数返回 dict，工具层返回 JSON 字符串（LLM 只吃文本）。
"""

import json

from langchain_core.tools import tool

from app.memory.vectorstore import retrieve, search_lore
from app.world.state import (
    get_world_state,
    transfer_item,
    update_quest,
    update_relationship,
)


def make_tools(player_id: int) -> list:
    """按玩家生成 6 个工具（3 读 + 3 写）。"""

    @tool
    def tool_get_world_state() -> str:
        """查询铁匠铺当前的世界状态：与玩家的关系、物品清单、任务进度。"""
        return json.dumps(get_world_state(player_id), ensure_ascii=False)

    @tool
    def tool_search_lore(user_text: str) -> str:
        """检索铁溪镇的世界观知识（地理、人物、物品、传闻）。"""
        return json.dumps(search_lore(user_text), ensure_ascii=False)

    @tool
    def tool_recall_memory(user_text: str) -> str:
        """检索关于当前玩家的长期记忆（玩家之前说过的话、偏好）。
        user_text 填玩家本轮的完整原话，原样传入。"""
        memories = retrieve(user_text, str(player_id), k=5)
        return json.dumps([{"key": m.key, "content": m.content_raw} for m in memories], ensure_ascii=False)

    @tool
    def tool_transfer_item(item_key: str, direction: str) -> str:
        """把某件物品给玩家（direction="give"）或从玩家收回（direction="take"）。
        物品代号如 iron_dagger（铁匕首）。"""
        return json.dumps(transfer_item(player_id, item_key, direction), ensure_ascii=False)

    @tool
    def tool_update_quest(clue: str) -> str:
        """把关于阿福去向的新线索记录进任务。线索如'有人在邻镇见过阿福'。"""
        return json.dumps(update_quest(clue), ensure_ascii=False)

    @tool
    def tool_update_relationship(delta: int, reason: str) -> str:
        """调整对玩家的信任值。delta 是变化量（正=更信任，负=更警惕），
        范围 -10 到 10。reason 说明为什么。"""
        return json.dumps(update_relationship(player_id, delta, reason), ensure_ascii=False)

    return [
        tool_get_world_state,
        tool_search_lore,
        tool_recall_memory,
        tool_transfer_item,
        tool_update_quest,
        tool_update_relationship
    ]
