"""LLM Judge：判那些查库和正则定不了的主观项，被 eval_runner 调用。

代价是花钱、有随机性，所以只判主观项，再用 Cohen's kappa 校准。这里是绝对评分
（判据成立与否），不做 A/B 配对比较。
"""
import json
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

# 裁判和被评必须不同厂商（被评 DeepSeek，裁判通义）：模型倾向给自己风格的输出打高分。
JUDGE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
JUDGE_MODEL = "qwen-plus"

_client = OpenAI(
    base_url=JUDGE_BASE_URL,
    api_key=os.getenv("DASHSCOPE_API_KEY", ""),
    # 不显式给就吃 SDK 默认（超时 600s × 重试 2 次），判官卡住会挂死整批。
    # 判官调用本身很短，120s 够用。
    timeout=float(os.getenv("MNEMOSYNE_TIMEOUT", "120")),
    max_retries=0,      # 重试交给下面的 for attempt 循环，那边有计数和错误记录
)

# 报告里要写清谁评的；换裁判做 kappa 也改这里
JUDGE_META = {"vendor": "通义 DashScope", "model": JUDGE_MODEL, "temperature": 0}

PROMPT_TEMPLATE = """你是严格的评测裁判。只判断下面这条判据是否成立，不要发散、不要补充判据。

【判据】
{rubric}

【对话记录】
{dialogue}

【待判的 NPC 回复（最后一轮）】
{reply}

只输出一个 JSON 对象，不要任何解释文字、不要代码块：
{{"pass": true 或 false, "score": 1 到 5, "reason": "一句话理由"}}
"""


def _strip_fence(text: str) -> str:
    """剥掉 ```json ... ``` 代码围栏。模型很爱裹，不剥就解析不了。"""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_verdict(text: str) -> dict:
    """从裁判输出里抠出 JSON，抠不出来抛 ValueError，由上层记成判官异常。"""
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 兜底：裁判偶尔在 JSON 前后多嘴，正则捞第一个 {...}
        m = re.search(r"\{.*\}", cleaned, re.S)
        if not m:
            raise ValueError(f"裁判输出里找不到 JSON：{text[:120]!r}")
        data = json.loads(m.group(0))
    if "pass" not in data:
        raise ValueError(f"裁判 JSON 缺 pass 字段：{data}")
    return data


def format_dialogue(scene: dict) -> str:
    """把 messages 和 replies 交错排成一段对话，多轮判据要看全过程。"""
    lines = []
    for i, msg in enumerate(scene["messages"]):
        lines.append(f"玩家：{msg}")
        if i < len(scene.get("replies", [])):
            lines.append(f"老陈：{scene['replies'][i]}")
    return "\n".join(lines) if lines else "（无对话，仅查库场景）"


def judge_scene(scene: dict, rubric: str, max_retries: int = 1):
    """对一个场景跑裁判，返回 (ok, detail, meta)。

    裁判自己出错时 ok 也是 False，但 meta["error"] 是 True —— 报告靠这个区分
    "模型没答好"和"判官没判成"，不让基础设施故障污染基线。
    """
    dialogue = format_dialogue(scene)
    reply = scene["replies"][-1] if scene.get("replies") else ""
    prompt = PROMPT_TEMPLATE.format(rubric=rubric, dialogue=dialogue, reply=reply)

    last_err = None
    for attempt in range(max_retries + 1):
        start = time.perf_counter()
        try:
            resp = _client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,           # 判分要可复现
                max_tokens=300,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            text = resp.choices[0].message.content or ""
            data = parse_verdict(text)
            ok = bool(data.get("pass"))
            score = data.get("score")
            reason = str(data.get("reason", "")).strip()
            detail = f"判官={JUDGE_MODEL} pass={ok} score={score} | 理由：{reason}"
            return ok, detail, {
                "score": score,
                "reason": reason,
                "latency_ms": round(latency_ms),
                "tokens": resp.usage.total_tokens,
                "error": False,
                "attempt": attempt + 1,
            }
        except Exception as e:                      # noqa: BLE001 裁判异常一律降级
            last_err = f"{type(e).__name__}: {' '.join(str(e).split())[:120]}"

    return False, f"判官异常（{max_retries + 1} 次都没判成）：{last_err}", {
        "score": None, "reason": "", "latency_ms": None,
        "tokens": None, "error": True, "attempt": max_retries + 1,
    }
