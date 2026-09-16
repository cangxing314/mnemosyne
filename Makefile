# 老陈铁匠铺（Mnemosyne）—— 常用命令入口
#
#   make            看这份清单
#   make dev        初始化 + 起 Web 服务  →  http://127.0.0.1:8000
#   make cli        命令行里直接和老陈聊
#
#  没装 make 也能跑项目——`make dev` 底下就是两行 python 命令，
#    直接照 README 的「快速开始」抄即可。make 只是省事，不是前置条件。

ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

.DEFAULT_GOAL := help
.PHONY: help init dev cli eval docker-up docker-down

help:
	@echo "make init        初始化：建表 + 灌种子 + 索引 lore（幂等，重复跑无害）"
	@echo "make dev         初始化后起 Web 服务（http://127.0.0.1:8000）"
	@echo "make cli         命令行版对话（不启网页）"
	@echo "make eval        全量评测：27 场景 x 3 次"
	@echo "make docker-up   容器方式起来（需 Docker Desktop）"
	@echo "make docker-down 停掉容器（数据留在命名卷里，不会丢）"

init:
	$(PY) scripts/bootstrap.py

dev:
	$(PY) scripts/bootstrap.py --serve

cli:
	$(PY) main.py

eval:
	$(PY) scripts/eval_runner.py

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
