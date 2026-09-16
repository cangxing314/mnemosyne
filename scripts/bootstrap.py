"""启动前初始化：建表 + 灌种子 + 索引 lore，三步都幂等。

world.db 和 data/chroma 都在 .gitignore 里，克隆下来是空的，不跑这一步直接起服务会在
search_lore 查到空 collection 时失败。加 --serve 则初始化完接着起 Web 服务。

"要不要灌种子"看的是表里有没有数据，不是看文件在不在：容器第一次启动时 create_all
已经建出空库文件了，看文件会误判。
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 脚本不在包内，直接跑时要把项目根塞进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

# override=True：以项目根 .env 为准，防系统里的旧环境变量压住它
load_dotenv(override=True)

from app.memory.vectorstore import index_lore
from app.world.models import Player, Quest, get_engine
from app.world.seed import seed


def bootstrap() -> None:
    """建表 → 空库灌种子 → lore 索引。三步都幂等。"""
    engine = get_engine()          # 顺带建表，create_all 幂等

    with Session(engine) as session:
        has_player = session.execute(select(Player).limit(1)).first() is not None
        has_quest = session.execute(select(Quest).limit(1)).first() is not None

    if not has_player:
        with Session(engine) as session:
            seed(session)
    elif not has_quest:
        # 半初始化状态，只可能是手工改库留下的。这里不能 seed：
        # seed 会先清表，把真实玩家一起冲掉。
        print("[warn] 库里已有玩家但没有任务行，跳过 seed；需要重置请手动删库重来")
    else:
        print("[skip] 世界状态已存在，跳过 seed")

    index_lore()                   # 内部幂等，collection 非空直接 return


def serve() -> None:
    """初始化完启动 Web 服务（uvicorn）。单 worker，原因见下。"""
    port = os.getenv("PORT", "8000")
    # 默认 127.0.0.1，这是浏览器能打开的地址。容器里由 compose 覆盖成 0.0.0.0
    # （容器必须绑所有网卡），但 0.0.0.0 本身访问不了，不能打给用户看。
    host = os.getenv("MNEMOSYNE_HOST", "127.0.0.1")
    # 走 sys.executable -m uvicorn 而不是直接调 uvicorn：前者不依赖 PATH，
    # 虚拟环境装出来的 uvicorn.exe 不一定在 PATH 上。
    cmd = [sys.executable, "-m", "uvicorn", "app.api:app", "--host", host, "--port", port]
    # 单 worker：sessions 是 app/api.py 的进程内字典，多 worker 会让同一 session_id
    # 落到不同进程、历史互相看不见，光改 --workers 数字没用。
    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("[serve] 打开浏览器访问  http://%s:%s" % (browse_host, port), flush=True)

    if os.name == "nt":
        # Windows 上没有真正的 exec：os.execvp 会直接抛 OSError [Errno 22]
        # Invalid argument。改成起子进程并等它，Ctrl+C 照样能传下去。
        try:
            sys.exit(subprocess.call(cmd))
        except KeyboardInterrupt:
            sys.exit(0)

    # POSIX（容器走这条）：真替换进程，停止信号直达 uvicorn
    os.execv(cmd[0], cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mnemosyne 启动前初始化（幂等）")
    parser.add_argument("--serve", action="store_true",
                        help="初始化完成后启动 Web 服务（本地和容器 CMD 都用这个）")
    args = parser.parse_args()

    bootstrap()
    if args.serve:
        serve()


if __name__ == "__main__":
    main()
