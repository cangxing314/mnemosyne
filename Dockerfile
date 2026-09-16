# 老陈铁匠铺（Mnemosyne）—— Web 版镜像：FastAPI + SSE 流式打字机
#
# 设计取舍（为什么不搞多阶段构建 / 不装 uv）：
#   这个镜像的目标只有一个——**别人拿到仓库能一键把 Demo 跑起来**。
#   所以链路越短越好：不依赖 ghcr.io 拉镜像（国内网络易挂）、不依赖 build backend、
#   不在镜像里重跑解析器。依赖按 .venv 里实测跑通的版本钉死（本地那套跑过
#   27 场景 × 多轮评测），换来的是可复现——版本一钉，镜像里跑的就是评测过的那套。
#
# 镜像大小约 1.4–1.6 GB：大头是 chromadb 拖进来的 onnxruntime + numpy。
#   实测 `import chromadb` **不会**真正加载 onnxruntime（只 import 了那层包装模块，
#   onnxruntime 要等用默认 embedding 函数时才 import），而本项目所有 embedding
#   都走 DashScope、显式传向量 → 用不到它。但它是 chromadb 的硬依赖，pip 一定会装，
#   删不掉；好在不影响功能，也不额外需要 libgomp 之类的系统库。
#
# 构建：docker build -t mnemosyne .
# 运行：docker compose up -d --build             ← 推荐（见 docker-compose.yml）
#       docker run --rm -p 8000:8000 --env-file .env -v mnemosyne-data:/app/data mnemosyne

FROM python:3.12-slim

# PYTHONUNBUFFERED：日志不缓冲，docker logs 能实时看到
# PYTHONDONTWRITEBYTECODE：容器里不需要 .pyc
# LANG/PYTHONIOENCODING：保证控制台中文（种子/索引的提示语）不乱码
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 LANG=C.UTF-8 PYTHONIOENCODING=utf-8

# 构建期显式关掉代理，并把 pip 源加进"不走代理"名单。
# 构建容器会继承宿主的代理设置（HTTP_PROXY 指向 host.docker.internal）；宿主代理
# 不可达时 pip 连代理都连不上，构建挂在装包这一步。pip 源用清华镜像（国内直连可达），
# 不需要代理，因此显式清空，构建结果不依赖宿主代理状态。
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY=pypi.tuna.tsinghua.edu.cn,localhost,127.0.0.1

WORKDIR /app

# 依赖版本与 pyproject.toml 一致（.venv 实测版本）；装包走清华源，国内构建不被墙
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple openai==3.8.0 python-dotenv==1.2.3 sqlalchemy==2.0.52 langgraph==1.2.11 langgraph-checkpoint-sqlite==3.1.1 chromadb==1.5.9 fastapi==0.141.1 uvicorn==0.52.4 langchain-core==1.6.2 langchain-openai==1.6.1

# 数据目录必须存在：SQLite 不会自动建目录（sqlite:///data/world.db 会直接报错）。
# 这里建空目录，真正的库文件由下面的 bootstrap 生成；compose 会把它挂成数据卷。
RUN mkdir -p /app/data

# 只拷运行时真正要用的东西（.dockerignore 已把 .venv/ data/ docs/ 挡在构建上下文外）
# static/ 必须整份进去：api.py 里挂载和返回首页用的都是相对路径 static/…，
# 所以容器的工作目录（WORKDIR）必须是项目根 /app，不能改成 app/。
COPY app/ ./app/
COPY static/ ./static/
COPY main.py ./
COPY scripts/bootstrap.py ./scripts/bootstrap.py

EXPOSE 8000

# 容器里必须绑 0.0.0.0（所有网卡），否则宿主机转发不进来。
# 本地直接 python 跑时不需要这个——bootstrap.py 的默认值是 127.0.0.1。
ENV MNEMOSYNE_HOST=0.0.0.0 PORT=8000

# 先初始化（建表 → 空库灌种子 → 索引 lore，三步都幂等），再起服务。
# 放在 CMD 里而不是另写 entrypoint.sh：Windows 上 .sh 会被 git 换成 CRLF 行尾，
# 进了容器 shebang 就废了——用 Python 起 Python，绕开这个坑。
CMD ["python", "scripts/bootstrap.py", "--serve"]
