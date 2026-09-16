"""向量检索（memories + lore 两个 collection）。

记忆两处存：memories 表存元数据、Chroma 存向量；写入两步，检索时按 id 回
SQLite 取全文。lore 没有表，原文直接存在 Chroma 的 documents 里。

两个 collection 分开：来源不同（世界观 vs 玩家）、lore 不变而记忆要沉底、
检索范围不同（问世界查 lore，问玩家查记忆）。命名一律沿用，不改第二套。
"""

import os

import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime
from app.memory.models import Memory
from app.persona.lore import LORE
from app.world.models import get_engine

# override=True：以项目根目录的 .env 为准，防系统残留的旧环境变量
load_dotenv(override=True)


def get_embedding(text: str) -> list[float]:
    """一句话 → 1024 维向量。"""
    client = OpenAI(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )
    resp = client.embeddings.create(
        model="text-embedding-v3",
        input=text,
    )
    return resp.data[0].embedding


def get_collection():
    """返回 memories collection（不存在会自动建）。"""
    client = chromadb.PersistentClient(path="data/chroma")
    return client.get_or_create_collection(name="memories")


def index_memory(mem: Memory) -> None:
    """记忆对象 → 向量 + 元数据，写进 Chroma，供语义检索。

    只存向量不存原文：全文在 SQLite，检索后按 id 回表取。
    """
    col = get_collection()
    col.add(
        ids=[str(mem.id)],                    # Chroma 主键（必须字符串）
        embeddings=[get_embedding(mem.content_raw)],   # 语义指纹
        metadatas=[{"user_id": mem.user_id, "key": mem.key}],  # 隔离/过滤用
    )


def retrieve(query: str, user_id: str, k: int = 5,
             time_decay: bool = True, importance: bool = True) -> list[Memory]:
    """按语义找该玩家最相关的 k 条记忆（相似度 × 时间衰减 × 重要性）。

    多捞 3 倍候选 → 三因子相乘重排 → 取 top-k。time_decay / importance 是
    消融开关，关掉某因子看通过率怎么变。
    """
    col = get_collection()
    result = col.query(
        query_embeddings=[get_embedding(query)],
        n_results=k * 3,                          # 多捞候选，重排后再取 top-k
        where={"user_id": user_id},
    )
    # Chroma 的 distances 越小越像，和 ids 一一对应，转成相似度
    distances = result["distances"][0]
    sim_map = {}
    for id_str, dist in zip(result["ids"][0], distances):
        sim_map[int(id_str)] = 1.0 / (1.0 + dist)   # 距离 0.21 对应相似度 0.83

    with Session(get_engine()) as session:
        memories = session.execute(
            select(Memory).where(Memory.id.in_(sim_map.keys()), Memory.is_active == True)
        ).scalars().all()

    def score_memory(mem: Memory) -> float:
        """综合分 = 语义相似度 × 时间衰减 × 重要性。"""
        sim = sim_map.get(mem.id, 0.0)
        if time_decay:
            age_hours = (datetime.now() - mem.created_at).total_seconds() / 3600
            decay = 0.5 ** (age_hours / 168)     # 7 天半衰期，过 7 天权重减半
        else:
            decay = 1.0
        if importance:
            imp = mem.importance / 10             # 1-10 归一化到 0.1-1
        else:
            imp = 1.0
        return sim * decay * imp

    scored = sorted(memories, key=score_memory, reverse=True)
    return scored[:k]


# lore 检索（第二个 collection）


def get_lore_collection():
    """返回 lore collection（不存在会自动建）。"""
    client = chromadb.PersistentClient(path="data/chroma")
    return client.get_or_create_collection(name="lore")


def index_lore() -> None:
    """把全部 lore 索引进 Chroma，幂等（库里非空就跳过）。

    与 index_memory 的区别：这里要传 documents——lore 没有表，原文只能存
    Chroma，检索时直接取回。
    """
    col = get_lore_collection()
    if col.count() > 0:
        return
    col.add(
        ids=[str(x["id"]) for x in LORE],
        embeddings=[get_embedding(x["text"]) for x in LORE],
        documents=[x["text"] for x in LORE],
        metadatas=[{"category": x["category"]} for x in LORE],
    )
    print("lore 索引完成")


def search_lore(user_text: str, k: int = 3) -> list[dict]:
    """按语义找最相关的 k 条 lore，返回 [{"category":..., "text":...}]。

    category 有用：传闻要加"听人说"前缀，带分类 LLM 才知道哪条是传闻。
    """
    col = get_lore_collection()
    result = col.query(query_embeddings=[get_embedding(user_text)], n_results=k)
    documents = result["documents"][0]
    metadatas = result["metadatas"][0]
    return [
        {"category": meta["category"], "text": text}
        for text, meta in zip(documents, metadatas)
    ]
