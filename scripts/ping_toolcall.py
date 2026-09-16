"""Function calling 格式验证（GLM-5.3 vs DeepSeek）。

探两家模型的工具调用返回格式，确认 OpenAI 兼容的 tools 参数能否直接用、结构是否一致。
跑法：.venv\\Scripts\\python.exe scripts\\ping_toolcall.py
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

# 一个最简单的工具定义：无参数的世界状态查询
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_world_state",
            "description": "查询铁匠铺当前的世界状态：和玩家的关系、物品清单、任务进度",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]

PROVIDERS = [
    {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.getenv("ZHIPU_API_KEY", ""),
        "model": "glm-5.3",
        "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
    },
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "model": "deepseek-chat",
        "extra_body": None,
    },
]


def test(provider: dict) -> None:
    print(f"\n=== {provider['name']} ===")
    client = OpenAI(base_url=provider["base_url"], api_key=provider["api_key"])
    kwargs = dict(
        model=provider["model"],
        messages=[{"role": "user", "content": "你现在铺子里都有什么货？"}],
        tools=TOOLS,
    )
    if provider.get("extra_body"):
        kwargs["extra_body"] = provider["extra_body"]
    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    print("回复内容:", repr(msg.content))
    print("tool_calls:", json.dumps(
        [{"id": tc.id, "name": tc.function.name, "args": tc.function.arguments}
         for tc in (msg.tool_calls or [])],
        ensure_ascii=False,
    ))
    print("finish_reason:", resp.choices[0].finish_reason)


if __name__ == "__main__":
    for p in PROVIDERS:
        test(p)
