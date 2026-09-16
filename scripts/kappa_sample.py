"""kappa 人工校准：样本抽样 + 盲标表生成。

主观场景（A1/A3/A5/A6/B1/B2/E4/E5/E6）没法用代码判，交给裁判模型 qwen-plus；
判官自身准不准靠人工复核来量：同批样本人判一遍、判官判一遍，算 Cohen's kappa。

抽样原则：每场景 6 条共 54 条；分层优先抽判 FAIL 的（最多 3 条）再用 PASS 补足，
避免一致性虚高；从 5 个消融格轮转着抽；固定种子，编号与内容可复现。

盲标表不含判官结果（否则不叫盲标），对照答案另存 kappa_answer_key.json。
用法：python scripts/kappa_sample.py
"""

import json
import os
import random

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(BASE, "data", "reports")
ABLATION_DIR = os.path.join(REPORTS, "ablation")
OUT_SHEET = os.path.join(REPORTS, "kappa盲标表.md")
OUT_KEY = os.path.join(REPORTS, "kappa_answer_key.json")

# 抽样覆盖的判官场景（顺序固定，编号才稳定）
JUDGE_SCENES = ["A1", "A3", "A5", "A6", "B1", "B2", "E4", "E5", "E6"]
PER_SCENE = 6
MAX_FAIL_PER_SCENE = 3   # 每场景最多抽几条 FAIL
SEED = 42

# 消融格顺序（轮转抽样用）
GRID_ORDER = ["full", "no_decay", "no_importance", "no_reflect", "naive"]


def load_sources() -> list:
    """收集所有可能有对话落盘的 JSON，返回 [(grid_name, payload), ...]。"""
    items = []
    if os.path.isdir(ABLATION_DIR):
        for fn in sorted(os.listdir(ABLATION_DIR)):
            if not fn.endswith(".json"):
                continue
            grid = fn[:-5]                       # full.json -> full
            p = os.path.join(ABLATION_DIR, fn)
            items.append((grid, p))
    # E 桶补跑（矩阵不跑 E 桶，单独一批）
    p = os.path.join(REPORTS, "kappa_e_bucket.json")
    if os.path.exists(p):
        items.append(("baseline", p))
    return items


def collect() -> tuple:
    """返回 (pool, notes)。pool[scene_id] = [样本 dict, ...]。"""
    pool = {sid: [] for sid in JUDGE_SCENES}
    notes = []
    for grid, path in load_sources():
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            notes.append(f"[跳过] {os.path.basename(path)}: {type(e).__name__} {e}")
            continue
        if not isinstance(d, dict) or "scenes" not in d:
            notes.append(f"[跳过] {os.path.basename(path)}: 无 scenes")
            continue
        for s in d["scenes"]:
            sid = s.get("id")
            if sid not in pool:
                continue
            dialogues = s.get("dialogues") or []
            passes = s.get("passes") or []
            details = s.get("details") or []
            for i, dlg in enumerate(dialogues):
                if not dlg:
                    continue
                pool[sid].append({
                    "scene": sid,
                    "name": s.get("name", ""),
                    "bucket": s.get("bucket", ""),
                    "grid": grid,
                    "run": i + 1,
                    "judge_pass": bool(passes[i]) if i < len(passes) else None,
                    "judge_detail": details[i] if i < len(details) else "",
                    "dialogue": dlg,
                    "expect": (s.get("expect") or "").strip(),
                })
    return pool, notes


def rotate_order(cands: list) -> list:
    """按消融格轮转排序：full#1, no_decay#1, ..., naive#1, full#2, ..."""
    by_grid = {}
    for c in cands:
        by_grid.setdefault(c["grid"], []).append(c)
    for g in by_grid:
        by_grid[g].sort(key=lambda x: x["run"])
    ordered = []
    depth = max((len(v) for v in by_grid.values()), default=0)
    for r in range(depth):
        for g in GRID_ORDER + [k for k in by_grid if k not in GRID_ORDER]:
            if g in by_grid and r < len(by_grid[g]):
                ordered.append(by_grid[g][r])
    return ordered


def pick(cands: list, n: int = PER_SCENE) -> list:
    """分层抽 n 条：FAIL 优先（最多 MAX_FAIL），PASS 补足；场景内打散顺序。"""
    ordered = rotate_order(cands)
    fails = [c for c in ordered if c["judge_pass"] is False]
    passes = [c for c in ordered if c["judge_pass"] is True]
    sel = fails[:MAX_FAIL_PER_SCENE]
    need = n - len(sel)
    sel = sel + passes[:need]
    if len(sel) < n:                      # FAIL+PASS 都不够（样本太少）
        rest = [c for c in ordered if c not in sel]
        sel = sel + rest[:n - len(sel)]
    rnd = random.Random(SEED + len(cands))
    rnd.shuffle(sel)                      # 打散，避免按格聚堆、露出规律
    return sel[:n]


def build():
    pool, notes = collect()
    samples = []
    gaps = []
    for sid in JUDGE_SCENES:
        cands = pool.get(sid, [])
        if not cands:
            gaps.append(sid)
            continue
        for c in pick(cands):
            samples.append(c)

    for i, s in enumerate(samples, 1):
        s["no"] = f"S{i:02d}"

    os.makedirs(os.path.dirname(OUT_SHEET), exist_ok=True)
    with open(OUT_SHEET, "w", encoding="utf-8") as f:
        f.write(render_sheet(samples, gaps))

    with open(OUT_KEY, "w", encoding="utf-8") as f:
        json.dump({
            "note": "kappa 对照答案：judge_pass 是裁判模型的判定，标完表再用 kappa_calc.py 对比",
            "per_scene": PER_SCENE,
            "scenes": JUDGE_SCENES,
            "samples": [{
                "no": s["no"], "scene": s["scene"], "grid": s["grid"], "run": s["run"],
                "judge_pass": s["judge_pass"], "judge_detail": s["judge_detail"],
            } for s in samples],
        }, f, ensure_ascii=False, indent=2)

    return samples, gaps, notes


def render_sheet(samples: list, gaps: list) -> str:
    L = []
    L.append("# kappa 人工校准盲标表")
    L.append("")
    L.append("## 怎么标（先读这段）")
    L.append("")
    L.append("- 每条给你：**一个场景 + 判据 + 完整对话**。你只需要判 **PASS** 或 **FAIL**。")
    L.append("- **判据看的是老陈的最后一轮回复**（前几轮是铺垫，别拿来判）。")
    L.append("- 凭你自己的理解判，**不要猜判官判了什么** —— 这张表就是要量你和它一致不一致。")
    L.append("- 表格里**故意不告诉你这条来自哪个配置**，也别找规律。")
    L.append("- 填法：把 `判定：` 后面写成 `PASS` 或 `FAIL`（理由可写可不写，写了以后复盘有用）。")
    L.append("")
    L.append(f"- 共 **{len(samples)} 条**"
             + (f"（缺 {'/'.join(gaps)}：这些场景没有历史对话落盘，需补跑）" if gaps else ""))
    L.append("")
    L.append("---")
    L.append("")

    cur_scene = None
    for s in samples:
        if s["scene"] != cur_scene:
            cur_scene = s["scene"]
            L.append(f"## 场景 {cur_scene} · {s['name']}")
            L.append("")
        L.append(f"### {s['no']}")
        L.append("")
        L.append("**判据**")
        L.append("")
        L.append("> " + (s["expect"].replace("\n", "\n> ") if s["expect"] else "（无判据文字）"))
        L.append("")
        L.append("**对话**")
        L.append("")
        L.append("```")
        L.append(s["dialogue"].strip())
        L.append("```")
        L.append("")
        L.append("**判定：**")
        L.append("")
        L.append("**理由（可选）：**")
        L.append("")
        L.append("---")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    samples, gaps, notes = build()
    print(f"样本 {len(samples)} 条 -> {OUT_SHEET}")
    if gaps:
        print(f"缺口场景：{gaps}")
    for n in notes:
        print(n)
