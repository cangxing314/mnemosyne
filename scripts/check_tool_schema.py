"""工具 schema 回归探针：确认下发给 LLM 的参数名没丢。

跑法：PYTHONPATH=. .venv/Scripts/python.exe scripts/check_tool_schema.py
"""
from app.agent.graph import langchain_to_openai
from app.agent.tools import make_tools


def main():
    ok = True
    for t in make_tools(1):
        params = langchain_to_openai(t)["function"]["parameters"]
        props = params.get("properties", {})
        required = params.get("required", [])
        print(f"{t.name:24s} type={params.get('type')} "
              f"properties={list(props)} required={required}")
        # 有签名的工具，properties 不能是空的（那正是参数丢失的症状）
        if t.args and not props:
            ok = False
            print(f"  ^^ 异常：该工具有参数 {list(t.args)}，但下发 schema 是空的")
    print("\n结果：" + ("PASS：参数名已正常下发" if ok else "FAIL：仍有工具参数丢失"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
