import os
from dotenv import load_dotenv
from openai import OpenAI

# 读取项目根目录的 .env 文件
load_dotenv(override=True)
client = OpenAI(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",   # DashScope 的 OpenAI 兼容入口
    api_key=os.getenv("DASHSCOPE_API_KEY"),
)

response = client.embeddings.create(
    model="text-embedding-v3",
    input="老陈记得旅行者说过他怕冷",
)
vec = response.data[0].embedding
print(f"向量长度: {len(vec)}")
print(f"前 5 个数: {vec[:5]}")