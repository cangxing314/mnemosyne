"""CLI 入口：.venv\\Scripts\\python.exe main.py，quit / exit / q / 退出 任一退出。

记忆不在循环变量里、在数据库里，所以同一个人跨会话是连续的。
"""

from app.agent.graph import build_graph
from app.world.state import get_or_create_player


def main() -> None:
    # 固定用"旅行者"这条记录，保证每次启动接上同一个玩家
    player_id = get_or_create_player("旅行者")

    app = build_graph()
    print("\n你来到铁溪镇，铁匠铺门口站着个中年男人。"
          "\n（铺子炉边挂着一副小号皮护手，墙上刻着几个歪扭的字。）")

    history: list[str] = []   # 最近对话，每轮带上，最多 8 条
    turn_count = 0            # 一问一答算一轮

    while True:
        user_message = input("你：").strip()
        if user_message.lower() in {"quit", "exit", "q", "退出"}:
            print("（你转身离开了铁匠铺）")
            break
        if not user_message:
            continue
        result = app.invoke({
            "player_id": player_id,
            "user_message": user_message,
            "turn": 0,                 # 本轮工具调用次数，每轮归零，防死循环
            "ablation": {},
            "round_no": turn_count,    # 玩家第几轮对话
            "used_tools": [],
            "history": "\n".join(history[-8:]),
        })
        npc_reply = result.get("npc_reply", "（老陈没说话）")
        print(f"\n老陈：{npc_reply}\n")
        history.append(f"玩家：{user_message}")
        history.append(f"老陈：{npc_reply}")
        turn_count += 1


if __name__ == "__main__":
    main()
