"""跨模型 sweep 驱动：换厂商换模型，验证长期记忆机制不依赖特定模型。

三条硬约束：一个模型起一个子进程（graph.py 的 client 在 import 时就建好了，同进程改
env 不生效）；判官固定 qwen-plus 不动，顺带满足"裁判与被评不同厂商"；记忆路径固定
--ablation full，全模型同口径。

用法：--only <模型> 只跑指定模型，--summary-only 只按已有报告重写汇总表，不传跑全部。
产物：data/reports/sweep/<slug>.json 与 _summary.md、_sweep.log（后台跑批看进度用）。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

DS_BASE = "https://api.deepseek.com"
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# (标签, 模型 id, 接口地址, 取哪个环境变量当 key)，顺序 = 跑的顺序：便宜的在前。
# 清单必须与报告一致，否则 clone 下来复现不出报告里的数字——要改清单先改报告，
# 两件事同一轮做。
# 三家：DeepSeek 官方 /models 的 flash 线（基准档）；智谱 GLM-5.3 在该网关返回
# 「product is not activated」，取可用的 glm-5.2；月之暗面 kimi-k2.7-code（第三方对照）。
MODELS = [
    ("deepseek-flash",      "deepseek-flash",      DS_BASE,        "DEEPSEEK_API_KEY"),
    ("glm-5.2",             "glm-5.2",             DASHSCOPE_BASE, "DASHSCOPE_API_KEY"),
    ("kimi-k2.7-code",      "kimi-k2.7-code",      DASHSCOPE_BASE, "DASHSCOPE_API_KEY"),
]

# 成本估算的量级单价（元 / 百万 token，输入输出混在一起粗估）。
# 未逐家核实实时价目，只给数量级；精确金额以各家账单为准。
PRICE_EST = {
    "deepseek-flash": 3.0,
    "glm-5.2": 10.0,
    "kimi-k2.7-code": 12.0,
}

# 汇总表里显示用的厂商名（与 MODELS 标签对应）
VENDOR = {
    "deepseek-flash": "DeepSeek",
    "glm-5.2": "智谱",
    "kimi-k2.7-code": "月之暗面",
}

OUT_DIR = PROJECT_ROOT / "data" / "reports" / "sweep"
LOG_PATH = OUT_DIR / "_sweep.log"

# 单个模型的跑批超时必须给足：eval_runner 只在全部场景跑完时才写 JSON，中途被杀
# = 该模型整轮白跑（没有断点续跑）。deepseek-flash 19 分钟跑完 27 场景；思考型模型
# 约 2–5 分钟/轮，给到 4 小时。
MODEL_TIMEOUT = 14400

# 冒烟：这几个场景各跑 1 次，确认模型能在 LangGraph 主链路里工作。
# A1 = 跨轮记忆（perceive/reflect），C1 = 工具调用（decide/act 的 function calling）。
SMOKE_IDS = ("A1", "C1")
SMOKE_TIMEOUT = 2400        # 40 分钟（2 个场景，留给思考型模型）


def log(msg: str) -> None:
    """同时打到 stdout 和日志文件（后台跑批时日志文件是唯一可靠凭证）。"""
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def slug_of(label: str) -> str:
    return label.replace("/", "_").replace(".", "_")


def build_env(model_id: str, base_url: str, key_env: str) -> dict:
    key = os.getenv(key_env, "")
    if not key:
        raise RuntimeError("环境变量 %s 为空，无法跑 %s" % (key_env, model_id))
    env = os.environ.copy()
    env["MNEMOSYNE_MODEL"] = model_id
    env["MNEMOSYNE_BASE_URL"] = base_url
    env["MNEMOSYNE_API_KEY"] = key
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def ping(model_id: str, base_url: str, key_env: str) -> tuple:
    """跑评测前先花几分钱验一次连通 + 模型名对不对。返回 (ok, 说明)。"""
    from openai import OpenAI
    try:
        c = OpenAI(base_url=base_url, api_key=os.getenv(key_env, ""))
        t0 = time.perf_counter()
        r = c.chat.completions.create(
            model=model_id, messages=[{"role": "user", "content": "回一个字：好"}], max_tokens=16)
        ms = int((time.perf_counter() - t0) * 1000)
        got = (r.choices[0].message.content or "").strip()
        return True, "%dms 回复=%r" % (ms, got[:20])
    except Exception as e:                            # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:140])


def check_smoke_report(tmp) -> tuple:
    """读冒烟产物，判断这次冒烟算不算过。返回 (pass?, 说明)。

    光看返回码不够：eval_runner 对单场景异常是记 errors 后继续，所以两个场景全被
    API 打回时 rc 依然是 0（曾遇到两个场景全 400、tokens_total=0 却报"冒烟 OK"）。
    单独抽出来也便于拿旧产物做回归测试。
    """
    try:
        rep = read_report(str(tmp))
    except Exception as e:                          # noqa: BLE001 — 产物坏了也算没过
        return False, "冒烟产物读不出来：%s" % str(e)[:140]
    runs = rep.get("total_runs") or 0
    passed = rep.get("total_pass") or 0
    errs = sum(1 for sc in (rep.get("scenes") or []) for e in (sc.get("errors") or []) if e)
    if errs:
        return False, "冒烟 %d/%d，但含 %d 个执行异常（接口/判官侧问题），不合格" % (
            passed, runs, errs)
    if runs == 0 or passed == 0:
        return False, "冒烟 %d/%d 全错，先查清模型与判据再跑全量" % (passed, runs)
    return True, "冒烟通过（%d/%d）" % (passed, runs)


def smoke(model_id: str, base_url: str, key_env: str) -> tuple:
    """在真实项目里先跑 2 个场景，确认模型能在 LangGraph 主链路里工作。

    API 能调通不等于项目里跑得通（工具调用格式、流式、JSON 解析都可能失败）。
    冒烟不通过即跳过。
    """
    tmp = OUT_DIR / ("_smoke_%s.json" % slug_of(model_id))
    cmd = [sys.executable, "scripts/eval_runner.py",
           "--only", ",".join(SMOKE_IDS), "--repeat", "1", "--ablation", "full",
           "--label", "smoke-%s" % model_id, "--out", str(tmp)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=build_env(model_id, base_url, key_env),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=SMOKE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "冒烟超时（>%.0f 分钟）" % (SMOKE_TIMEOUT / 60)
    if p.returncode != 0 or not tmp.exists():
        tail = ((p.stderr or "") + "\n" + (p.stdout or "")).strip().splitlines()[-3:]
        return False, "冒烟失败 rc=%s：%s" % (p.returncode, " | ".join(tail)[:260])
    ok, msg = check_smoke_report(tmp)
    return (ok, "%s（%.0f 秒）" % (msg, time.time() - t0)) if ok else (False, msg)


def run_one(label: str, model_id: str, base_url: str, key_env: str, force: bool) -> dict:
    """跑一个模型，返回 {label, status, out, returncode, seconds}。"""
    out_path = OUT_DIR / ("%s.json" % slug_of(label))
    started = time.time()

    if out_path.exists() and not force:
        log("%s -> 已有 %s，跳过（--force 可重跑）" % (label, out_path.name))
        return {"label": label, "status": "skipped", "out": str(out_path),
                "returncode": None, "seconds": 0.0}

    ok, msg = ping(model_id, base_url, key_env)
    if not ok:
        log("%s -> 连通性失败，跳过：%s" % (label, msg))
        return {"label": label, "status": "ping_failed", "out": None,
                "returncode": None, "seconds": time.time() - started}
    log("%s -> 连通 OK（%s）" % (label, msg))

    ok2, msg2 = smoke(model_id, base_url, key_env)
    log("%s -> %s：%s" % (label, "冒烟 OK" if ok2 else "**冒烟 FAIL**", msg2))
    if not ok2:
        return {"label": label, "status": "smoke_failed", "out": None,
                "returncode": None, "seconds": time.time() - started}

    log("%s -> 开跑 27 场景…" % label)

    cmd = [sys.executable, "scripts/eval_runner.py",
           "--ablation", "full",
           "--label", label,
           "--out", str(out_path)]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=build_env(model_id, base_url, key_env),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=MODEL_TIMEOUT)
        rc = proc.returncode
        tail = (proc.stdout or "").strip().splitlines()[-6:]
        for t in tail:
            log("    | %s" % t)
        if rc != 0:
            err = (proc.stderr or "").strip().splitlines()[-4:]
            for t in err:
                log("    ! %s" % t)
    except subprocess.TimeoutExpired:
        log("%s -> **超时**（> %.0f 分钟）被杀、无产物，该模型本轮白跑；"
            "要重跑请先加大 MODEL_TIMEOUT" % (label, MODEL_TIMEOUT / 60))
        return {"label": label, "status": "timeout", "out": None,
                "returncode": None, "seconds": time.time() - started}

    secs = time.time() - started
    status = "ok" if (rc == 0 and out_path.exists()) else "failed"
    log("%s -> %s（%.1f 分钟，rc=%s）" % (label, status, secs / 60, rc))
    return {"label": label, "status": status, "out": str(out_path) if out_path.exists() else None,
            "returncode": rc, "seconds": secs}


def read_report(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summarize(runs: list = None) -> str:
    """把 data/reports/sweep/ 下所有已完成的报告汇总成一张对比表。

    扫目录而不是只看本次传入的 runs：sweep 支持 --only 分批跑，只看 runs 会漏掉
    之前批次跑完的模型。runs 只用来补"失败/超时/跳过"这类没产出文件的格子。
    """
    secs_of = {r["label"]: r.get("seconds") for r in (runs or [])}

    rows = []
    for path in sorted(OUT_DIR.glob("*.json")):
        if path.name.startswith("_"):          # _smoke_kimi.json 等辅助产物不算正式模型
            continue
        try:
            d = read_report(str(path))
        except Exception:                      # noqa: BLE001 半截文件不该拖垮整张汇总
            continue
        label = d.get("label") or path.stem
        scenes = d.get("scenes") or []
        bucket = {}
        err_runs = 0                           # 含执行异常的 run 数（见表格尾注）
        for sc in scenes:
            b = sc.get("bucket") or "?"
            passes = [p for p in (sc.get("passes") or []) if isinstance(p, bool)]
            bucket.setdefault(b, {"pass": 0, "run": 0})
            bucket[b]["pass"] += sum(1 for p in passes if p)
            bucket[b]["run"] += len(passes)
            err_runs += sum(1 for e in (sc.get("errors") or []) if e)
        tot_run = d.get("total_runs") or sum(v["run"] for v in bucket.values())
        tot_pass = d.get("total_pass")
        if tot_pass is None:
            tot_pass = sum(v["pass"] for v in bucket.values())
        tok = (d.get("usage") or {}).get("tokens_total") or 0
        price = PRICE_EST.get(label)
        secs = secs_of.get(label)
        rows.append({
            "label": label, "status": "ok",
            "model": d.get("model"), "judge": (d.get("judge") or {}).get("model"),
            "pass": tot_pass, "run": tot_run,
            "rate": (tot_pass / tot_run * 100) if tot_run else 0.0,
            "bucket": bucket, "tokens": int(tok), "err_runs": err_runs,
            "cost_est": (tok / 1e6 * price) if price else None,
            "minutes": (secs / 60) if secs else None,
        })

    have = {r["label"] for r in rows}
    for r in (runs or []):                     # 没产出文件的（连通失败/超时）补一行状态
        if r["label"] not in have:
            rows.append({"label": r["label"], "status": r["status"]})

    lines = []
    lines.append("# 跨模型 sweep 汇总")
    lines.append("")
    lines.append("> 生成时间：%s ｜ 判官固定 qwen-plus ｜ 记忆路径 --ablation full（向量检索）"
                 % datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("")
    lines.append("| 模型 | 厂商 | 总通过 | A 记忆 | B 立场 | C 工具 | D 任务 | E 人设 | token | 估成本 | 耗时 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    def bucket_cell(b, key):
        v = b.get(key)
        if not v or not v["run"]:
            return "—"
        return "%d/%d" % (v["pass"], v["run"])

    # 通过率后面挂异常标记：含"执行异常"的 run 是接口/判官侧失败、不是模型答错，
    # 平均进去会把数字压低还看不出原因（曾有模型首轮 60.5%，其中 32/81 个 run 是欠费 400）。
    def rate_cell(row):
        s = "**%d/%d = %.1f%%**" % (row["pass"], row["run"], row["rate"])
        if row.get("err_runs"):
            s += " ⚠️%d 异常" % row["err_runs"]
        return s

    for row in rows:
        if row.get("status") != "ok":
            lines.append("| %s | — | %s | — | — | — | — | — | — | — | — |" % (row["label"], row["status"]))
            continue
        b = row["bucket"]
        cost = ("¥%.1f" % row["cost_est"]) if row.get("cost_est") is not None else "—"
        mins = ("%.0f 分" % row["minutes"]) if row.get("minutes") else "—"
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["label"], VENDOR.get(row["label"], "—"),
            rate_cell(row),
            bucket_cell(b, "A"), bucket_cell(b, "B"), bucket_cell(b, "C"),
            bucket_cell(b, "D"), bucket_cell(b, "E"),
            "{:,}".format(row["tokens"]), cost, mins))

    lines.append("")
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    if ok_rows:
        tot_tok = sum(r["tokens"] for r in ok_rows)
        tot_cost = sum(r["cost_est"] for r in ok_rows if r.get("cost_est") is not None)
        lines.append("合计 token：**%s** ｜ 估算成本：**≈ ¥%.1f**（价目为量级粗估，以账单为准）"
                     % ("{:,}".format(tot_tok), tot_cost))
        lines.append("")
        lo = min(r["rate"] for r in ok_rows)
        tied = [r["label"] for r in ok_rows if r["rate"] == lo]
        lines.append("最低通过率：**%.1f%%**（%s）" % (lo, "、".join(tied)))
        dirty = [r for r in ok_rows if r.get("err_runs")]
        if dirty:
            lines.append("")
            lines.append("> ⚠️ **含执行异常的报告**（数字偏低、非模型能力）："
                         + "、".join("`%s`（%d 个 run）" % (r["label"], r["err_runs"]) for r in dirty))
            lines.append("> 执行异常 = 接口返回错误（如欠费 400）或判官解析失败，"
                         "**不可直接与其它模型比较**，应先补跑这些场景再汇总。")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="跨模型 sweep")
    parser.add_argument("--only", type=str, default=None,
                        help="只跑指定模型，逗号分隔（如 kimi-k2.5,glm-5.2）")
    parser.add_argument("--force", action="store_true", help="已跑过的也重跑")
    parser.add_argument("--ping-only", action="store_true", help="只验连通，不跑评测")
    parser.add_argument("--summary-only", action="store_true",
                        help="不跑评测，只按目录里已有报告重写 _summary.md（补跑/分批跑后用）")
    parser.add_argument("--repeat", type=int, default=None, help="透传给 eval_runner 的重复次数")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.summary_only:                      # 不占用模型额度，只重算汇总表
        summary = summarize()
        (OUT_DIR / "_summary.md").write_text(summary, encoding="utf-8")
        log(summary)
        log("汇总已写：%s" % (OUT_DIR / "_summary.md"))
        return

    log("=" * 56)

    targets = MODELS
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        targets = [m for m in MODELS if m[0] in want]
        if not targets:
            log("--only 没匹配到任何模型，可选项：%s" % ", ".join(m[0] for m in MODELS))
            return
    log("sweep 启动：%d / %d 个模型，输出到 %s" % (len(targets), len(MODELS), OUT_DIR))

    runs = []
    for label, mid, base, key_env in targets:
        if args.ping_only:
            ok, msg = ping(mid, base, key_env)
            log("%s -> %s %s" % (label, "OK" if ok else "FAIL", msg))
            runs.append({"label": label, "status": "ok" if ok else "ping_failed",
                         "out": None, "returncode": None, "seconds": 0.0})
            continue
        runs.append(run_one(label, mid, base, key_env, args.force))

    if not args.ping_only:
        summary = summarize(runs)
        (OUT_DIR / "_summary.md").write_text(summary, encoding="utf-8")
        log("-" * 56)
        for line in summary.splitlines():
            if line.startswith("|") or line.startswith("合计") or line.startswith("最低"):
                log(line)
        log("汇总已写：%s" % (OUT_DIR / "_summary.md"))

    ok_n = sum(1 for r in runs if r["status"] == "ok")
    log("sweep 结束：成功 %d / %d" % (ok_n, len(runs)))


if __name__ == "__main__":
    main()
