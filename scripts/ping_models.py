"""连通性测试：ping 智谱 GLM 和 DeepSeek。

两家都走 OpenAI 兼容接口，需要 base_url + api_key（项目根目录 .env）+ model。
跑法：.venv\\Scripts\\python.exe scripts\\ping_models.py
"""

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

# 读取项目根目录的 .env 文件
load_dotenv(override=True)

PROVIDERS = [
    {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.getenv("ZHIPU_API_KEY", ""),
        "model": "glm-5.3",
        # GLM-5.3 是常思考模型：thinking.type 只能 enabled，强度用 reasoning_effort
        # （官方文档：low / high / max，开发期用 low 省 token 降延迟）
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

PROMPT = "只回复两个字：乒乓"


def ping(provider: dict) -> None:
    print(f"\n=== {provider['name']}（{provider['model']}）===")

    if not provider["api_key"]:
        print("跳过：.env 里没填这一家的 key")
        return

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
    )

    kwargs = dict(
        model=provider["model"],
        messages=[{"role": "user", "content": PROMPT}],
        max_tokens=200,
    )
    if provider.get("extra_body"):
        kwargs["extra_body"] = provider["extra_body"]

    start = time.perf_counter()
    try:
        resp = client.chat.completions.create(**kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        reply = resp.choices[0].message.content.strip()
        shown = reply if reply else "（空回复）"
        print(f"回复：{shown}")
        print(f"延迟：{elapsed_ms:.0f} 毫秒")
        print(f"token：输入 {resp.usage.prompt_tokens} / 输出 {resp.usage.completion_tokens}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"失败（{elapsed_ms:.0f} 毫秒后报错）：{e}")


def main() -> None:
    print("ping_models —— 检查大模型接口连通性")
    for p in PROVIDERS:
        ping(p)
    print("\n完成。")


if __name__ == "__main__":
    main()
