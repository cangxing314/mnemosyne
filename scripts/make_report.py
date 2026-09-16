"""基线报告生成器：把跑批 JSON 渲染成人读的 Markdown。

单独成脚本是为了可复现——改进前后对比必须从两份 JSON 机械生成，手写会随代码失真。
产物默认写到跟 JSON 同名的 .md；--compare 旧.json 追加「前后对比」章节。
跑法：.venv\\Scripts\\python.exe scripts/make_report.py [JSON路径]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def latest_json() -> Path:
    """找 data/reports 里最新的 baseline_*.json。"""
    files = sorted(Path("data/reports").glob("baseline_*.json"))
    if not files:
        raise SystemExit("data/reports 下没有 baseline_*.json——先跑 scripts/eval_runner.py")
    return files[-1]


def rate_cell(rec: dict) -> str:
    """通过率格：全过写 100%，部分写 n/N + 标记。"""
    n, k = len(rec["passes"]), sum(rec["passes"])
    if k == n:
        return f"✅ {k}/{n}"
    if k == 0:
        return f"❌ 0/{n}"
    return f"⚠️ {k}/{n}（flaky）"


def scene_index(payload: dict) -> dict:
    """按场景 id 建索引：id -> (通过数, 总数)。"""
    return {r["id"]: (sum(r["passes"]), len(r["passes"])) for r in payload["scenes"]}


def compare_section(cur: dict, base: dict) -> list:
    """「前后对比」章节：把两份跑批 JSON 摆在一起比。

    改进前后对比、跨模型 sweep 都用这段。先做可比性自检（模型/判官/重复次数），
    不一致就显式警告——数字能不能比是事实问题，不能让读者自己想。
    """
    out = []
    add = out.append
    add(f"## 六、前后对比（{base.get('label') or '对比批'} → {cur.get('label') or '本批'}）")
    add("")
    warns = []
    if base.get("model") != cur.get("model"):
        warns.append(f"被评模型不同：`{base.get('model')}` vs `{cur.get('model')}`")
    bj, cj = base.get("judge") or {}, cur.get("judge") or {}
    if (bj.get("model"), bj.get("temperature")) != (cj.get("model"), cj.get("temperature")):
        warns.append(f"判官不同：`{bj.get('model')}` vs `{cj.get('model')}`")
    if base.get("repeat") != cur.get("repeat"):
        warns.append(f"重复次数不同：{base.get('repeat')} vs {cur.get('repeat')}")
    if warns:
        add("> ⚠️ **两批数字不可直接比**：")
        for w in warns:
            add(f"> - {w}")
        add("")

    b_t, b_n = base["total_pass"], base["total_runs"]
    c_t, c_n = cur["total_pass"], cur["total_runs"]
    add(f"**总体：{b_t}/{b_n} = {base['overall_rate']:.1%} → {c_t}/{c_n} = "
        f"{cur['overall_rate']:.1%}（{c_t - b_t:+d} 题）**")
    add("")

    add("| 桶 | 含义 | 对比批 | 本批 | 变化 |")
    add("|---|---|---|---|---|")
    for key, b in base["buckets"].items():
        c = cur["buckets"].get(key)
        if not c:
            continue
        delta = c["mean"] - b["mean"]
        flag = "" if abs(delta) < 1e-9 else (" 🔼" if delta > 0 else " 🔽")
        add(f"| {key} | {b['name']} | {b['mean']:.0%} ± {b['std']:.0%} | "
            f"{c['mean']:.0%} ± {c['std']:.0%} | {delta:+.0%}{flag} |")
    add("")

    base_idx, cur_idx = scene_index(base), scene_index(cur)
    changed = []
    for sid, (ct, cn) in cur_idx.items():
        bt, bn = base_idx.get(sid, (0, 0))
        if (ct, cn) != (bt, bn):
            changed.append((sid, bt, bn, ct, cn))
    if changed:
        add("**通过率有变化的场景：**")
        add("")
        add("| 场景 | 对比批 | 本批 | 变化 |")
        add("|---|---|---|---|")
        for sid, bt, bn, ct, cn in changed:
            add(f"| {sid} | {bt}/{bn} | {ct}/{cn} | {ct - bt:+d} |")
        add("")
    else:
        add("所有场景的通过率都与对比批一致。")
        add("")
    add(f"其余 {len(cur_idx) - len(changed)} 个场景持平；本批共 {len(cur_idx)} 个场景。")
    add("")
    add("> 对比批的数字读自它的 JSON（未重跑）；要看对比批的全貌，读那一批自己的报告。")
    add("")
    return out


def render(payload: dict, compare: dict | None = None) -> str:
    """payload 是 eval_runner.save_json 写出的 dict。

    compare 传另一批的 payload 时，末尾追加「前后对比」章节。
    """
    stats = payload["usage"]
    overall = payload["overall_rate"]
    lines = []
    add = lines.append

    add(f"# {payload.get('label') or '评测'}报告（{payload['date']}）")
    add("")
    add("> 本文件由 `scripts/make_report.py` 从跑批 JSON 自动生成，**不要手改**——")
    add("> 改了下次跑批会被覆盖，要改就改生成逻辑或场景定义。")
    add("")
    judge = payload.get("judge")
    add("## 一、元信息")
    add("")
    if payload.get("model"):
        note = f"（{payload['model_note']}）" if payload.get("model_note") else ""
        add(f"- 被评模型：`{payload['model']}`{note}")
    add(f"- 每场景重复次数：**{payload['repeat']}**（单次结果不可作数）")
    add(f"- 跑批用例：**{len(payload['scenes'])} 场景 / {payload['total_runs']} runs**")
    if judge:
        add(f"- 裁判模型：{judge['vendor']} `{judge['model']}`（temperature={judge['temperature']}）"
            "——与被评模型不同厂商，避免「自己评自己」的偏好偏差")
    add("")

    add("## 二、总览")
    add("")
    add(f"**总体通过率：{payload['total_pass']}/{payload['total_runs']} = {overall:.1%}**")
    add("")
    add("| 桶 | 含义 | 场景数 | 通过率 mean±std |")
    add("|---|---|---|---|")
    for key, b in payload["buckets"].items():
        add(f"| {key} | {b['name']} | {b['scenes']} | {b['mean']:.0%} ± {b['std']:.0%} |")
    add("")
    add("> std 是**桶内各场景通过率之间的标准差**，不是重复跑的标准差——它衡量的是"
        "「这一桶里场景难度是否齐整」，越大说明同一桶里有的场景全过、有的全挂，"
        "此时只看桶均值会掩盖问题，必须往下看分场景表。")
    add("")

    add("## 三、分场景明细")
    add("")
    add("| 场景 | 名称 | 类型 | 通过率 | P50 延迟 | token | 考什么（判据首行） |")
    add("|---|---|---|---|---|---|---|")
    for rec in payload["scenes"]:
        kind = "Judge" if rec["kind"] == "judge" else "客观锚"
        review = " (待确认)" if rec.get("needs_review") else ""
        lat = rec.get("latencies", [])
        p50 = f"{sorted(lat)[len(lat) // 2]:.0f}ms" if lat else "—"
        tok = str(sum(rec.get("tokens", []))) if rec.get("tokens") else "—"
        add(f"| {rec['id']} | {rec['name']}{review} | {kind} | {rate_cell(rec)} | {p50} | {tok} | {rec.get('expect', '')} |")
    add("")

    failed = [r for r in payload["scenes"] if sum(r["passes"]) < len(r["passes"])]
    add("### 未满分场景的失败详情")
    add("")
    if not failed:
        add("（无——全部场景都满分）")
        add("")
    else:
        for rec in failed:
            add(f"**{rec['id']} {rec['name']}**（{rate_cell(rec)}）")
            add("")
            for i, (ok, detail, err) in enumerate(zip(rec["passes"], rec["details"], rec["errors"])):
                mark = "PASS" if ok else ("ERR" if err else "FAIL")
                add(f"- 第 {i + 1} 次 [{mark}]：{detail}")
            reps = rec.get("fail_replies", [])
            if reps:
                # 原文必须进报告：只写"命中现代词=['效率']"判不出是模型错还是断言错。
                # E1 那次失败原文是「"效率"我听懂了，你夸我打铁快」，实际是断言误判。
                add("")
                add("失败那几轮老陈的原话：")
                add("")
                for j, r in enumerate(reps, 1):
                    add(f"> 【{j}】{r.strip()}")
                    add("")
            add("")

    add("## 四、延迟与成本")
    add("")
    add("| 指标 | 值 |")
    add("|---|---|")
    add(f"| 延迟 P50 | {stats['latency_p50']:.0f} ms |")
    add(f"| 延迟 P95 | {stats['latency_p95']:.0f} ms |")
    add(f"| 延迟均值 | {stats['latency_mean']:.0f} ms |")
    add(f"| 样本数（轮） | {stats['latency_samples']} |")
    add(f"| token 总量 | {stats['tokens_total']} |")
    add(f"| 每轮 token 均值 | {stats['tokens_per_turn_mean']:.0f} |")
    add(f"| 成本**上限** | ¥{stats['cost_upper_cny']:.4f} |")
    add("")
    add("> 「每轮」= 一条玩家消息触发的全部 LLM 调用之和（decide(+act 回环)+respond+reflect），"
        "即玩家发出消息到收到回复的等待时长。")
    add("> 成本是**上限**：`turn_log` 只记了 total_tokens，没拆输入/输出、也不知道缓存命中，"
        "所以按「全部 token 都按输出价、且按高峰价」估，结果为上限。")
    add("")

    add("## 五、读这份报告必须知道的事")
    add("")
    add("1. **单次结果不可作数**：本项目已有实证——A2 场景同代码同断言，前一天 FAIL、次日三连 PASS。")
    add("   所以一切结论看 `repeat` 次的比例，不看去掉重复的「跑一次」。")
    add("2. **客观锚 vs Judge 的可信度不同**：客观锚查库/正则，确定性；Judge 有随机性，")
    add("   需用 Cohen's kappa 交叉校准后才好当硬指标。")
    add("3. **基线里的失败不等于「模型差」**：已知代码缺陷会成片拉低某些桶，")
    add("   （背景：工具参数 schema 曾被换成空对象 → 写工具全线失败）")
    add("   （ReAct 回环曾没接上 → 同一轮可能重复调工具）。修完要重跑同一批对比。")
    add("")
    if compare:
        lines.extend(compare_section(payload, compare))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="把跑批 JSON 渲染成 Markdown 报告")
    parser.add_argument("json_path", nargs="?", default=None,
                        help="baseline_*.json 路径；不填则取 data/reports 里最新的")
    parser.add_argument("-o", "--out", default=None,
                        help="输出的 .md 路径；默认与 JSON 同名（改后缀）")
    parser.add_argument("--label", default=None,
                        help="覆盖报告标题里的批次名（JSON 里 label 为空时用它补，"
                             "如 --label baseline）")
    parser.add_argument("--model", default=None,
                        help="覆盖被评模型名（旧版跑批 JSON 缺 model 字段时用它补，"
                             "值应从沙盒 turn_log 取证，别凭印象填）")
    parser.add_argument("--compare", default=None,
                        help="另一批 baseline_*.json 的路径；给出则在报告末尾追加「前后对比」"
                             "章节（改进前后 / 跨模型 sweep 都用它）")
    args = parser.parse_args()

    src = Path(args.json_path) if args.json_path else latest_json()
    payload = json.loads(src.read_text(encoding="utf-8"))
    if args.label:
        payload["label"] = args.label
    if args.model:
        payload["model"] = args.model
    compare = None
    if args.compare:
        compare = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    md = render(payload, compare=compare)

    out = Path(args.out) if args.out else src.with_suffix(".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"源：{src}")
    print(f"已生成：{out}（{len(md)} 字符）")


if __name__ == "__main__":
    main()
