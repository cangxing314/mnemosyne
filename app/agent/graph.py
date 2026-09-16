"""Agent 状态图：perceive -> decide -> (act -> decide)* -> respond -> reflect。

act -> decide 是 ReAct 回环，靠 turn 计数收口（见 route_after_decide）。
"""
import sqlite3
import json
import os
import re
import time
from typing import TypedDict
from langgraph.checkpoint.sqlite import SqliteSaver
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session
from langgraph.config import get_stream_writer
from langchain_openai import ChatOpenAI
from app.agent.prompts import (GOAL_PROMPT, NO_TOOL_RULE, REFLECT_PROMPT,
                               SUMMARIZE_PROMPT, SYSTEM_PROMPT, TOOL_RULES)
from app.agent.rules import judge_relationship_change
from app.agent.tools import make_tools
from app.agent.trace import log_turn
from app.memory.models import Memory
from app.memory.store import supersede, find_active
from app.memory.vectorstore import index_memory, search_lore, retrieve
from app.world.models import get_engine
from app.world.state import get_world_state, update_relationship

load_dotenv(override=True)


MODEL_NAME = os.getenv("MNEMOSYNE_MODEL", "deepseek-flash")
MODEL_BASE_URL = os.getenv("MNEMOSYNE_BASE_URL", "https://api.deepseek.com")
MODEL_API_KEY = os.getenv("MNEMOSYNE_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")


MODEL_TIMEOUT = float(os.getenv("MNEMOSYNE_TIMEOUT", "120"))   # 单次请求读超时（秒）
MODEL_RETRIES = int(os.getenv("MNEMOSYNE_RETRIES", "2"))       # 失败自动重试次数

client = OpenAI(base_url=MODEL_BASE_URL, api_key=MODEL_API_KEY,
                timeout=MODEL_TIMEOUT, max_retries=MODEL_RETRIES)

# 摘要层：每 5 轮把旧摘要和新对话合并重压，同 key 覆盖、旧版自动沉底。
# 评测场景最长 3 轮，够不到阈值，300 runs 矩阵的数字不受影响。
SUMMARY_EVERY_TURNS = 5
SUMMARY_KEY = "summary"          # 固定 key，滚雪球靠同 key 覆盖实现
SUMMARY_IMPORTANCE = 8           # 长会话里它是最该被想起来的那条背景


class GraphState(TypedDict):
    """节点之间传的状态包。

    turn 是本次请求内调了几次工具（每轮归零，防 ReAct 死循环），round_no 是玩家
    第几轮对话——两者混用会让工具提前被禁（CLI 踩过）。llm_ms / llm_tokens 累计
    本轮所有 LLM 调用，由 reflect 写进 turn_log，报告的 P50/P95 取的就是它。
    """
    player_id: int
    user_message: str
    turn: int
    round_no: int
    history: str
    world_snapshot: dict
    memories_text: str
    summary_text: str
    lore_text: str
    used_tools: list[str]
    ablation: dict
    tool_call: dict | None
    tool_result: str
    npc_reply: str
    llm_ms: int
    llm_tokens: int


def perceive(state: GraphState) -> dict:
    """装配节点：世界快照 + 生效记忆 + 本轮 lore，都转成文本行存进 state。

    summary 虽然也在记忆表里，但要单独拎出来——它是压缩过的背景，混进逐条原文
    里会被模型当成原话引用。
    """
    world_snapshot = get_world_state(state["player_id"])
    ablation = state.get("ablation") or {}
    if ablation.get("architecture") == "naive":
        memories = []  # naive：不要长期记忆
    elif ablation.get("memory_source") == "retrieve":
        memories = retrieve(state["user_message"], str(state["player_id"]), k=5,
    time_decay=ablation.get("time_decay", True),
    importance=ablation.get("importance", True),
        )
    else:
        with Session(get_engine()) as session:
            memories = session.execute(
                select(Memory).where(
                    Memory.user_id == str(state["player_id"]),
                    Memory.is_active == True)
            ).scalars().all()
    summary_text = ""
    mem_lines = []
    for m in memories:
        if m.key == SUMMARY_KEY:
            summary_text = m.content_raw
        else:
            mem_lines.append(f"· {m.key}：{m.content_raw}")
    memories_text = "\n".join(mem_lines)
    lore_hits = search_lore(state["user_message"], k=3)
    lore_text = "\n".join(x["text"] for x in lore_hits)
    return {
        "world_snapshot": world_snapshot,
        "memories_text": memories_text,
        "summary_text": summary_text,
        "lore_text": lore_text,
    }


def build_context(state: GraphState) -> str:
    """把 perceive 取到的几源拼成给 LLM 的文本（respond 复用同一批数据，排版不同）。

    摘要段排在事实记忆前面，先粗后细，模型不容易把概括当成原话。
    """
    world_snapshot = state["world_snapshot"]
    quests_text = "、".join(
        f"{q['title']}(stage {q['stage']})" for q in world_snapshot["quests"]
    )
    items_text = "、".join(
        f"{i['item_name']}({i['holder']})" for i in world_snapshot["inventory"]
    )
    ctx = f"""【最近的对话】
{state.get("history", "")}

【世界状态】
信任: {world_snapshot["trust"]} | 好感: {world_snapshot["affection"]}
任务: {quests_text}
物品: {items_text}"""
    summary_text = state.get("summary_text", "")
    if summary_text:
        ctx += f"""

【早前的事（摘要）】
{summary_text}"""
    ctx += f"""

【你对这个玩家的记忆】
{state["memories_text"]}

【本轮相关的镇上知识】
{state["lore_text"]}"""
    tool_result = state.get("tool_result", "")
    if tool_result:
        ctx += "\n\n【工具返回】\n" + tool_result
    return ctx


def langchain_to_openai(t) -> dict:
    """LangChain 工具对象 -> OpenAI tools 参数格式。

    LangChain 给无参数工具生成的 schema 是 type:null，OpenAI 要求必须是 object；
    而 t.args 只是 properties 映射、没有 type 键（真 schema 在 t.tool_call_schema），
    照它判断会恒真、把真参数换成空对象，LLM 只能瞎猜参数名。C2/D2/D3/D4/D6 都栽在这。
    """
    schema = t.tool_call_schema.model_json_schema()
    # 只留 OpenAI 认的三个键，schema 里的 title/description 不用下发
    params = {k: schema[k] for k in ("type", "properties", "required") if k in schema}
    if params.get("type") != "object":
        params = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": params,
        },
    }


def decide(state: GraphState) -> dict:
    """调工具还是直接回复。有 tool_calls 就存进 state，没有就留 None 走 respond。"""
    tools_openai = [langchain_to_openai(t) for t in make_tools(state["player_id"])]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + GOAL_PROMPT + "\n\n" + TOOL_RULES},
        {"role": "user", "content": build_context(state) + "\n\n【玩家本轮的话】\n" + state["user_message"]},
    ]
    # decide 在回环里会跑多次，这里的耗时和 token 是累加到 state 上的，不是覆盖
    start = time.perf_counter()
    resp = client.chat.completions.create(model=MODEL_NAME,
                                          messages=messages, tools=tools_openai)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    used_tokens = resp.usage.total_tokens if resp.usage else 0
    tc = resp.choices[0].message.tool_calls
    if tc:
        tool_call = {"name": tc[0].function.name,
                     "args": json.loads(tc[0].function.arguments)}
    else:
        tool_call = None
    return {"tool_call": tool_call,
            "llm_ms": state.get("llm_ms", 0) + elapsed_ms,
            "llm_tokens": state.get("llm_tokens", 0) + used_tokens}


def act(state: GraphState) -> dict:
    """执行 LLM 选中的工具，结果回灌 state，turn 加一。

    两处参数兜底：模型习惯把参数名填成 query（我们叫 user_text），偶尔干脆不填。
    检索输入本来就该是玩家本轮原话，都在代码层补上，不让一个参数失误炸掉整批。
    """
    tools = {t.name: t for t in make_tools(state["player_id"])}
    tool_call = state["tool_call"]
    args = tool_call["args"]
    # 模型习惯把参数名填成 query，我们这边叫 user_text，这里翻译一下
    if "query" in args and "user_text" not in args:
        args = {"user_text": args["query"]}
    # 空参数兜底：不填就用玩家本轮原话。缺了会导致 pydantic 抛 required，
    # 整批评测跑挂（C2 踩过）
    if tool_call["name"] in ("tool_recall_memory", "tool_search_lore") and not args.get("user_text"):
        args = {"user_text": state["user_message"]}
    result = tools[tool_call["name"]].invoke(args)
    return {"tool_result": result, "turn": state["turn"] + 1,
            "used_tools": state.get("used_tools", []) + [tool_call["name"]]}


# 混进台词的工具调用文本长这样，只匹配这些形状，避免误伤正常台词。
_TOOL_TAG = (r"(?:tool_call|invoke|parameter|tool_\w+|update_quest|update_relationship"
             r"|transfer_item|get_world_state|search_lore|recall_memory)")
_TOOL_TEXT_PATTERNS = [
    # 成对标签，以及模型拿工具名直接当标签用的情况
    re.compile(rf"<({_TOOL_TAG})[\s\S]*?</\1>", re.I),
    # 只有一半的孤立标签
    re.compile(rf"</?{_TOOL_TAG}[^>]*>", re.I),
    # 裸 JSON
    re.compile(r'\{\s*"name"\s*:\s*"[^"]*"\s*,\s*"arguments"\s*:[\s\S]*?\}\s*\}?', re.I),
]


def _strip_tool_text(text: str) -> str:
    """删掉混进台词的工具调用文本。

    respond 不给 tools，模型想调工具就只能打字，台词里会混进 <tool_call>…，
    玩家看得见。NO_TOOL_RULE 是主防线，这里兜底。
    """
    for p in _TOOL_TEXT_PATTERNS:
        text = p.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        print("respond 输出全是工具文本，清洗后为空")
    return text


def respond(state: GraphState) -> dict:
    """生成老陈的最终回复，不给工具。

    tool_result 用 get 取——没调工具时这个键从没被填过，直接下标会 KeyError。
    """
    context = build_context(state)
    user_content = context + "\n\n【玩家本轮的话】\n" + state["user_message"]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + GOAL_PROMPT + "\n\n" + NO_TOOL_RULE},
        {"role": "user", "content": user_content}
    ]

    # 逐块广播、同时攒全文。流式默认不返回用量，stream_usage=True 才拿得到
    writer = get_stream_writer()
    parts = []
    used_tokens = 0
    llm = ChatOpenAI(
        model=MODEL_NAME,
        base_url=MODEL_BASE_URL,
        api_key=MODEL_API_KEY,
        stream_usage=True,
        timeout=MODEL_TIMEOUT,          # 流式最容易挂住不回，超时必须给
        max_retries=MODEL_RETRIES,
    )
    start = time.perf_counter()
    for chunk in llm.stream(messages):
        token = chunk.content or ""
        if token:
            writer({"token": token})
            parts.append(token)
        if getattr(chunk, "usage_metadata", None):
            used_tokens = chunk.usage_metadata.get("total_tokens", 0)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return {"npc_reply": _strip_tool_text("".join(parts)),
            "llm_ms": state.get("llm_ms", 0) + elapsed_ms,
            "llm_tokens": state.get("llm_tokens", 0) + used_tokens}


def _maybe_summarize(round_no: int, user_id: str, history: str, acc: dict) -> None:
    """到轮次就把这段对话压成摘要写库（滚雪球）。

    取出生效的旧摘要连同新对话交 LLM 合并重写，同 key 覆盖，永远只有一条生效。
    只在 SUMMARY_EVERY_TURNS 的整数倍轮触发（round_no 是 0 基，第 5、10、15… 轮）。
    用量累加进 acc 不返回，触发那轮的延迟和 token 会明显高一截。
    """
    if (round_no + 1) % SUMMARY_EVERY_TURNS != 0:
        return

    with Session(get_engine()) as session:
        old = find_active(session, user_id=user_id, key=SUMMARY_KEY)
    old_text = old.content_raw if old is not None else "（还没有备忘，这是第一次）"

    # 用 replace 不用 format，正文里的花括号会被 format 当占位符
    filled_prompt = (SUMMARIZE_PROMPT
                     .replace("{old_summary}", old_text)
                     .replace("{dialogue}", history))
    start = time.perf_counter()
    resp = client.chat.completions.create(
        model=MODEL_NAME, messages=[{"role": "system", "content": filled_prompt}])
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    used_tokens = resp.usage.total_tokens if resp.usage else 0
    # 先记账，下面任何提前返回都不会漏掉这次用量
    acc["llm_ms"] += elapsed_ms
    acc["llm_tokens"] += used_tokens

    # 自由文本没有 JSON 那样的边界，只能削掉模型爱加的前缀和反引号
    text = (resp.choices[0].message.content or "").strip().strip("`").strip()
    for prefix in ("摘要：", "摘要:", "备忘：", "备忘:", "【摘要】", "【备忘】"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        return

    with Session(get_engine()) as session:
        new_mem = supersede(session, user_id=user_id, key=SUMMARY_KEY,
                            content_raw=text, kind="summary",
                            importance=SUMMARY_IMPORTANCE, source_turn=round_no)
        # index_memory 必须在 with 里：出了块 session 就关，mem.id
        # 一访问就 DetachedInstanceError
        index_memory(new_mem)      # 摘要也进向量库，它会被当作一条记忆检索到


def reflect(state: GraphState) -> dict:
    """从本轮对话里抽值得长期记住的事实，写进记忆库。

    走 supersede 不走 add_memory——玩家改口时同 key 被新版本盖掉，只留最新一版。
    """
    skip_reflect = state.get("ablation", {}).get("reflection") is False
    facts = []
    # 先垫上 decide/respond 的累计，reflect 自己那份在下面加；不能放进 if，末尾要用
    acc = {"llm_ms": state.get("llm_ms", 0), "llm_tokens": state.get("llm_tokens", 0)}
    if not skip_reflect:
        dialogue = "玩家:" + state["user_message"] + "\n老陈:" + state.get("npc_reply", "")
        # 用 replace 不用 format：prompt 里 JSON 示例的花括号会被当占位符
        filled_prompt = REFLECT_PROMPT.replace("{dialogue}", dialogue)
        messages = [{"role": "system", "content": filled_prompt}]
        start = time.perf_counter()
        resp = client.chat.completions.create(model=MODEL_NAME, messages=messages)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        used_tokens = resp.usage.total_tokens if resp.usage else 0
        # 续进累计，decide/respond 已经各自加过自己那份
        acc["llm_ms"] += elapsed_ms
        acc["llm_tokens"] += used_tokens
        text = (resp.choices[0].message.content or "").strip()
        # 模型爱加"好的，结果如下："或代码围栏，只截 JSON 数组本体，否则解析必失败
        lo, hi = text.find("["), text.rfind("]")
        if lo != -1 and hi > lo:
            text = text[lo:hi + 1]

        try:
            facts = json.loads(text)
        except json.JSONDecodeError:
            print("reflect 抽取解析失败，跳过本轮（LLM 输出非 JSON）")
            return acc
        with Session(get_engine()) as session:
            for f in facts:
                new_mem = supersede(session, user_id=str(state["player_id"]), key=f["key"],
                                    content_raw=f["content_raw"], kind=f["kind"],
                                    source_turn=state.get("round_no", 0))
                index_memory(new_mem)

        # 必须留在 if 里：摘要是反思的一部分，no_reflect 那格要连它一起关
        summary_input = (state.get("history", "")
                         + f"\n玩家：{state['user_message']}"
                         + f"\n老陈：{state.get('npc_reply', '')}")
        _maybe_summarize(state.get("round_no", 0), str(state["player_id"]),
                         summary_input, acc)

    # 信任值走写死的规则表，才能让"说线索 trust 必 +2"这种断言精确复现
    delta, reason = judge_relationship_change(state["user_message"])
    if delta != 0:
        update_relationship(state["player_id"], delta, reason)

    # 只记录，不改业务；评测报告全靠这张表
    log_turn(
        player_id=state["player_id"],
        turn=state.get("round_no", 0),
        user_message=state["user_message"],
        npc_reply=state.get("npc_reply", ""),
        # 记全部工具不是最后一个：一轮内可能先记线索再给信物，只留最后一个会失真
        tool_name=",".join(state.get("used_tools", [])) or None,
        facts=facts,
        trust_delta=delta,
        reason=reason if delta != 0 else None,
        model=MODEL_NAME,
        latency_ms=acc["llm_ms"],
        tokens=acc["llm_tokens"],
    )

    return acc


def route_after_decide(state: GraphState) -> str:
    """decide 之后的路口：还有工具可用就去 act，否则去 respond。

    turn < 3 是防死循环上限；另外挡一层同工具不重复——模型会把同一件事拆成两次
    调用，update_quest 的幂等挡不住，stage 会连涨两格。
    """
    tc = state["tool_call"]
    if tc and state["turn"] < 3 and tc["name"] not in state.get("used_tools", []):
        return "act"
    else:
        return "respond"


def build_graph(use_checkpoint: bool = True):
    """组装状态图。act -> decide 那条回环边就是 ReAct。

    use_checkpoint=False 给 SSE 用——SqliteSaver 不支持异步 astream，而流式那条路
    的 reflect 照常写库，历史恢复不依赖 checkpoint。
    """
    graph = StateGraph(GraphState)
    graph.add_node("perceive", perceive)
    graph.add_node("decide", decide)
    graph.add_node("act", act)
    graph.add_node("respond", respond)
    graph.add_node("reflect", reflect)
    graph.add_edge(START, "perceive")
    graph.add_edge("perceive", "decide")
    graph.add_conditional_edges("decide", route_after_decide,
                                {"act": "act", "respond": "respond"})
    graph.add_edge("act", "decide")
    graph.add_edge("respond", "reflect")
    graph.add_edge("reflect", END)
    if use_checkpoint:
        conn = sqlite3.connect("data/checkpoints.db", check_same_thread=False)
        saver = SqliteSaver(conn)
        return graph.compile(checkpointer=saver)
    return graph.compile()
