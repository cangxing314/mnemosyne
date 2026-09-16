"""A2 断言的变异测试：喂"必须判挂"的假数据，看断言会不会真 FAIL。

恒真的断言比没有断言更危险。A2 不只看回复还查库（看生效那条 residence），
所以得先造一个"记忆已更新"的库状态，这是 build_db() 的活。
跑法：.venv\\Scripts\\python.exe scripts/mutate_a2.py（末尾应打印 4/4）
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

import scripts.eval_runner as R

SANDBOX = "data/sandbox/_a2_mutation"


def build_db(case_id, active_content, sunk_content):
    """造一个库：residence 两条，一条生效、一条沉底（sunk 为 None 则只留生效条）。

    每个用例用独立文件名，不删旧文件，重跑直接覆盖。
    """
    Path(SANDBOX).mkdir(parents=True, exist_ok=True)
    db = f"{SANDBOX}/{case_id}.db"
    if not os.path.exists(db):
        shutil.copy("data/world.db", db)

    import app.world.models as world_models
    world_models.DB_PATH = db

    from app.memory.models import Memory
    with Session(world_models.get_engine()) as s:
        for m in s.execute(select(Memory).where(Memory.key == "residence")).scalars().all():
            s.delete(m)
        s.commit()
        if sunk_content is not None:
            s.add(Memory(user_id="1", kind="semantic", key="residence",
                         content_raw=sunk_content, version=1, is_active=False))
            s.commit()
        s.add(Memory(user_id="1", kind="semantic", key="residence",
                     content_raw=active_content, version=2, is_active=True))
        s.commit()
    return db


def old_assert_logic(rows, reply):
    """旧写法（.first() 取表里第一条），用来对照证明它会假 PASS。"""
    row = rows[0] if rows else None
    return row is not None and "灰烬山脉" in row.content_raw and "灰烬山脉" in reply


def run_case(case_id, label, active, sunk, reply, expect_new):
    build_db(case_id, active, sunk)
    import app.world.models as world_models
    from app.memory.models import Memory
    with Session(world_models.get_engine()) as s:
        rows = s.execute(select(Memory).where(Memory.key == "residence")
                         ).scalars().all()
    new_ok, detail = R.assert_cross_session_memory({"replies": [reply]})
    verdict = "OK " if new_ok == expect_new else "!!!"
    print(f"[{verdict}] {label}")
    old_ok = old_assert_logic(rows, reply)
    flag = "   [旧写法在此处假 PASS]" if (old_ok and not new_ok) else ""
    print(f"      旧写法={'PASS' if old_ok else 'FAIL'}{flag}")
    print(f"      新写法={'PASS' if new_ok else 'FAIL'}（期望 {'PASS' if expect_new else 'FAIL'}）")
    print(f"      {detail}")
    return new_ok == expect_new


print("=" * 70)
print("A2 assert_cross_session_memory 变异测试")
print("=" * 70)
results = []
results.append(run_case(
    "c1", "正常：生效=灰烬山脉，回复=灰烬山脉（应 PASS）",
    "玩家住在灰烬山脉脚下", None, "灰烬山脉脚下——你自己上回说的。", True))
results.append(run_case(
    "c2", "关键：玩家改住王都（旧值沉底），回复却说灰烬山脉（应 FAIL）",
    "玩家住在王都", "玩家住在灰烬山脉脚下", "灰烬山脉脚下——你自己上回说的。", False))
results.append(run_case(
    "c3", "改住址后答对（生效=王都，回复=王都）→ 断言锚定初值，应 FAIL",
    "玩家住在王都", "玩家住在灰烬山脉脚下", "王都啊，你自己说的。", False))
results.append(run_case(
    "c4", "空回复（应 FAIL）", "玩家住在灰烬山脉脚下", None, "", False))

print()
print(f"通过 {sum(results)}/{len(results)}")
print("变异测试全过" if all(results) else "!!! 有断言未按预期失败")
