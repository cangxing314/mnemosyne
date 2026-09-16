"""记忆系统单测。

10 个断言场景，覆盖写入 / 版本沉底 / 版本链 / key 隔离 / 玩家隔离 / 检索过滤。
约定每个测试用独立 user_id（u1~u10），自己准备数据，测试间互不污染。
跑法：.venv\\Scripts\\python.exe scripts\\test_memory.py（全通过打印 10/10 PASS）
"""

import sys
from pathlib import Path

# 直接运行本脚本时，Python 默认只在 scripts/ 里找模块；
# 把项目根目录加进搜索路径，才能 import app 包。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.memory.store import add_memory, find_active, list_active, supersede
from app.memory.vectorstore import index_memory, retrieve
from app.world.models import get_engine


def test_1(session: Session) -> None:
    mem = add_memory(session, user_id="u1", kind="episodic", key="residence",
                     content_raw="我住北京")
    assert mem.version == 1, "新记忆 version 应为 1"
    assert mem.is_active is True, "新记忆应处于生效状态"


def test_2(session: Session) -> None:
    old = add_memory(session, user_id="u2", kind="episodic", key="residence",
                     content_raw="我住北京")
    new = supersede(session, user_id="u2", key="residence", content_raw="我搬杭州了")
    assert old.is_active is False, "覆盖后旧记忆 is_active 应为 False"


def test_3(session: Session) -> None:
    old = add_memory(session, user_id="u3", kind="episodic", key="residence",
                     content_raw="我住北京")
    new = supersede(session, user_id="u3", key="residence", content_raw="我搬杭州了")
    assert old.superseded_by == new.id, "旧记忆 superseded_by 应指向新记忆 id"


def test_4(session: Session) -> None:
    old = add_memory(session, user_id="u4", kind="episodic", key="residence",
                     content_raw="我住北京")
    new1 = supersede(session, user_id="u4", kind="episodic", key="residence",
                     content_raw="我住长春")
    new2 = supersede(session, user_id="u4", kind="episodic", key="residence",
                     content_raw="我住上海")
    assert new2.version == 3, "两次覆盖后 version 应为 3"


def test_5(session: Session) -> None:
    old = add_memory(session, user_id="u5", kind="episodic", key="residence",
                     content_raw="我住北京")
    new = supersede(session, user_id="u5", kind="episodic", key="residence",
                    content_raw="我搬杭州了")
    assert find_active(session, user_id="u5", key="residence").content_raw == "我搬杭州了", \
        "find_active 应返回最新内容"


def test_6(session: Session) -> None:
    old = add_memory(session, user_id="u6", kind="episodic", key="residence",
                     content_raw="我住北京")
    new1 = supersede(session, user_id="u6", kind="episodic", key="residence",
                     content_raw="我住北京")
    new2 = supersede(session, user_id="u6", kind="episodic", key="residence",
                     content_raw="我住北京")
    assert len(list_active(session, "u6")) == 1, "两次覆盖后生效记忆应只剩 1 条"


def test_7(session: Session) -> None:
    old = add_memory(session, user_id="u7", kind="episodic", key="residence",
                     content_raw="我住北京")
    new1 = supersede(session, user_id="u7", kind="episodic", key="residence",
                     content_raw="我住北京")
    new2 = supersede(session, user_id="u7", kind="episodic", key="residence",
                     content_raw="我住北京")
    assert old.superseded_by == new1.id and new1.superseded_by == new2.id \
        and new2.superseded_by is None, "三代版本链应完整衔接"


def test_8(session: Session) -> None:
    old1 = add_memory(session, user_id="u8", kind="episodic", key="residence",
                      content_raw="我住北京")
    old2 = add_memory(session, user_id="u8", kind="episodic", key="weapon_pref",
                      content_raw="我住北京")
    new1 = supersede(session, user_id="u8", kind="episodic", key="residence",
                     content_raw="我住北京")
    assert old2.is_active is True, "覆盖 residence 不应影响 weapon_pref"


def test_9(session: Session) -> None:
    old1 = add_memory(session, user_id="u9a", kind="episodic", key="residence",
                      content_raw="我住北京")
    old2 = add_memory(session, user_id="u9b", kind="episodic", key="residence",
                      content_raw="我住上海")
    assert len(list_active(session, "u9a")) == 1, "u9a 应有 1 条生效记忆"
    assert list_active(session, "u9a")[0].content_raw == "我住北京", "u9a 的内容应是北京"
    assert len(list_active(session, "u9b")) == 1, "u9b 应有 1 条生效记忆"
    assert list_active(session, "u9b")[0].content_raw == "我住上海", "u9b 的内容应是上海"


def test_10(session: Session) -> None:
    old = add_memory(session, user_id="u10", kind="episodic", key="residence",
                     content_raw="我住北京")
    index_memory(old)
    new = supersede(session, user_id="u10", kind="episodic", key="residence",
                    content_raw="我搬杭州了")
    index_memory(new)
    results = retrieve("我住在哪里", "u10", k=5)
    assert all(mem.content_raw != "我住北京" for mem in results), \
        "检索不应返回已沉底的记忆"


def main() -> None:
    with Session(get_engine()) as session:
        test_1(session)
        test_2(session)
        test_3(session)
        test_4(session)
        test_5(session)
        test_6(session)
        test_7(session)
        test_8(session)
        test_9(session)
        test_10(session)
    print("10/10 PASS")


if __name__ == "__main__":
    main()
