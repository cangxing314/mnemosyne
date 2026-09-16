"""kappa 校准：读盲标表 + 对照答案，算 Cohen's kappa。

不能只看一致率——判官几乎全判 PASS 时一致率能冲到 94%+，但那只是 PASS 太多、闭着眼
填也能蒙对。kappa 把瞎猜蒙对的部分扣掉：
    κ = (实际一致率 - 瞎猜期望一致率) / (1 - 瞎猜期望一致率)
样本偏斜时 κ 方差大，同时输出 PABAK（2×一致率-1）。

用法：先把 kappa盲标表.md 的"判定："逐条填 PASS/FAIL，再跑本脚本，
结果写 data/reports/kappa_result.md。
"""

import json
import math
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHEET = os.path.join(BASE, "data", "reports", "kappa盲标表.md")
KEY = os.path.join(BASE, "data", "reports", "kappa_answer_key.json")
OUT = os.path.join(BASE, "data", "reports", "kappa_result.md")

# 标注可能写英文或中文，都接受；FAIL 的写法必须先判，
# 否则"不通过"会被 PASS 的"通过"误吞。
FAIL_WORDS = ("FAIL", "不通过", "不符", "挂", "错", "否", "NO", "✗", "×", "X")
PASS_WORDS = ("PASS", "通过", "符合", "过", "对", "是", "YES", "✓", "√", "O")


def parse_sheet() -> tuple:
    """解析盲标表，返回 ({no: bool}, 缺失编号列表)。

    True=人判 PASS，False=人判 FAIL，None=没填。只在"判定"与"理由"标记之间取值：
    判据文字里本身带"判 FAIL"字样（A1 的判据原文就有），不限定范围会被误读。
    """
    with open(SHEET, "r", encoding="utf-8") as f:
        text = f.read()

    labels = {}
    blocks = re.findall(r"###\s+(S\d+)(.*?)(?=###\s+S\d+|\Z)", text, re.S)
    for no, seg in blocks:
        m = re.search(r"\*\*判定\s*[：:]\s*\*{0,2}(.*?)(?=\*\*理由|$)", seg, re.S)
        body = (m.group(1) if m else "").strip()
        if not body:
            labels[no] = None
            continue
        up = body.upper()
        if any(w in up for w in FAIL_WORDS):
            labels[no] = False
        elif any(w in up for w in PASS_WORDS):
            labels[no] = True
        else:
            labels[no] = None

    missing = [no for no, v in sorted(labels.items()) if v is None]
    return labels, missing


def cohen_kappa(pairs: list) -> dict:
    """pairs = [(human_bool, judge_bool), ...] -> 各项指标。"""
    n = len(pairs)
    a = sum(1 for h, j in pairs if h and j)          # 都判 PASS
    b = sum(1 for h, j in pairs if h and not j)      # 人 PASS / 判官 FAIL
    c = sum(1 for h, j in pairs if not h and j)      # 人 FAIL / 判官 PASS
    d = sum(1 for h, j in pairs if not h and not j)  # 都判 FAIL

    po = (a + d) / n
    p_h_pass = (a + b) / n
    p_j_pass = (a + c) / n
    pe = p_h_pass * p_j_pass + (1 - p_h_pass) * (1 - p_j_pass)
    kappa = (po - pe) / (1 - pe) if pe != 1 else float("nan")
    pabak = 2 * po - 1
    # κ 的近似标准误（Cohen 1960 简化式）
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2)) if pe != 1 else float("nan")

    return {"n": n, "a": a, "b": b, "c": c, "d": d, "po": po, "pe": pe,
            "kappa": kappa, "pabak": pabak, "se": se}


def verdict(k: float) -> str:
    if k != k:            # NaN
        return "无法计算"
    if k < 0.2:
        return "差（几乎等于瞎猜，判官不可信）"
    if k < 0.4:
        return "一般（判官判据需重写）"
    if k < 0.6:
        return "中等（可用，但要在报告里写明局限）"
    if k < 0.8:
        return "较好（可以往外说，附样本量）"
    return "高度一致（判官可信）"


def main():
    with open(KEY, "r", encoding="utf-8") as f:
        key = json.load(f)
    ans = {s["no"]: s for s in key["samples"]}

    labels, missing = parse_sheet()
    if missing:
        print(f"还有 {len(missing)} 条没标：{' '.join(missing)}")
        print("标完再跑一次。")
        if len(missing) == len(labels):
            return

    pairs = []
    rows = []
    for no in sorted(ans):
        h = labels.get(no)
        if h is None:
            continue
        j = ans[no]["judge_pass"]
        pairs.append((h, j))
        rows.append((no, ans[no]["scene"], ans[no]["grid"], ans[no]["run"], h, j))

    st = cohen_kappa(pairs)

    L = []
    L.append("# kappa 人工校准结果")
    L.append("")
    L.append(f"- 已标样本：**{st['n']} 条**"
             + (f"（另有 {len(missing)} 条未标）" if missing else ""))
    L.append(f"- 判官模型：{key.get('note', '')[:0]}qwen-plus（temperature=0）")
    L.append("")
    L.append("## 主指标")
    L.append("")
    L.append("| 指标 | 值 | 含义 |")
    L.append("|---|---|---|")
    L.append(f"| 一致率 | {st['po']:.1%} | 你俩判一样的比例 |")
    L.append(f"| 瞎猜期望一致率 | {st['pe']:.1%} | 闭眼填也能凑出的比例 |")
    L.append(f"| **Cohen's κ** | **{st['kappa']:.3f}** | 扣掉瞎猜后的一致性 → {verdict(st['kappa'])} |")
    L.append(f"| PABAK | {st['pabak']:.3f} | 偏斜样本上更稳的对照值 |")
    L.append(f"| κ 标准误（近似） | {st['se']:.3f} | 越小越稳 |")
    L.append("")
    L.append(f"- κ 的经验门槛：<0.2 差 / 0.2–0.4 一般 / 0.4–0.6 中等 / 0.6–0.8 较好 / >0.8 高度一致")
    L.append(f"- 样本偏斜提醒：判官判 PASS {st['a'] + st['c']} 条、FAIL {st['b'] + st['d']} 条。"
             f"PASS 占比过高时 κ 本身方差大，**请与 PABAK 一起看**。")
    L.append("")
    L.append("## 混淆矩阵")
    L.append("")
    L.append("|  | 判官判 PASS | 判官判 FAIL |")
    L.append("|---|---|---|")
    L.append(f"| **你判 PASS** | {st['a']} | {st['b']} |")
    L.append(f"| **你判 FAIL** | {st['c']} | {st['d']} |")
    L.append("")
    L.append("## 逐条对照")
    L.append("")
    L.append("| 编号 | 场景 | 配置 | run | 你 | 判官 | 一致 |")
    L.append("|---|---|---|---|---|---|---|")
    for no, scene, grid, run, h, j in rows:
        agree = "是" if h == j else "**否**"
        L.append(f"| {no} | {scene} | {grid} | {run} | {'PASS' if h else 'FAIL'} | "
                 f"{'PASS' if j else 'FAIL'} | {agree} |")
    L.append("")

    disagree = [(no, sc, g, r, h, j) for no, sc, g, r, h, j in rows if h != j]
    L.append("## 分歧清单（复盘用）")
    L.append("")
    if not disagree:
        L.append("无分歧。")
    else:
        for no, sc, g, r, h, j in disagree:
            L.append(f"- **{no}**（{sc}）你判 {'PASS' if h else 'FAIL'} / 判官判 "
                     f"{'PASS' if j else 'FAIL'} → 回表里看这条，想清楚谁对、为什么")
    L.append("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"样本 {st['n']} 条")
    print(f"一致率 {st['po']:.1%} / 瞎猜期望 {st['pe']:.1%}")
    print(f"Cohen's kappa = {st['kappa']:.3f}  ({verdict(st['kappa'])})")
    print(f"PABAK = {st['pabak']:.3f} / SE = {st['se']:.3f}")
    print(f"分歧 {len(disagree)} 条")
    print(f"结果已存：{OUT}")


if __name__ == "__main__":
    main()
