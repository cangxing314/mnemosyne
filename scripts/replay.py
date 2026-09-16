"""对话回放：把某玩家的全部轮次按顺序打印（评测调试用）。

运行：.venv\\Scripts\\python.exe scripts\\replay.py
"""

import sys
from pathlib import Path

# 直接运行本脚本时，Python 默认只在 scripts/ 里找模块；把项目根加进搜索路径。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.world.models import TurnLog, get_engine


def replay(player_id: int = 1) -> None:
    """按轮次顺序回放一个玩家的完整对话过程。"""
    with Session(get_engine()) as session:
        logs = session.query(TurnLog).filter(
            TurnLog.player_id == player_id
        ).order_by(TurnLog.turn).all()

    if not logs:
        print(f"玩家 {player_id} 还没有对话记录")
        return

    print(f"=== 玩家 {player_id} 对话回放（{len(logs)} 轮）===")
    for log in logs:
        print(f"\n-- 第 {log.turn} 轮 --")
        print(f"玩家：{log.user_message}")
        print(f"老陈：{log.npc_reply}")
        if log.tool_name:
            print(f"   [调用了工具 {log.tool_name}]")
        if log.facts:
            for f in log.facts:
                print(f"   [记忆 {f.get('key', '?')}: {f.get('content_raw', f)}]")
        if log.trust_delta != 0:
            print(f"   [信任 {log.trust_delta:+d}：{log.reason}]")


if __name__ == "__main__":
    replay(1)
