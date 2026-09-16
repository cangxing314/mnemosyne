"""逐格跑消融矩阵，支持断点续跑。

每格单独写一个 JSON，中断后重新拉起会跳过已跑完的格。必须串行：5 格共用全局
Chroma 和 data/sandbox/{场景}，并行会互相污染。不传参数跑全部 5 格。
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)

from scripts.eval_runner import ABLATION_GRID, REPEAT, ablation_ids

IDS = ",".join(ablation_ids())
OUT_DIR = "data/reports/ablation"
os.makedirs(OUT_DIR, exist_ok=True)

master = open("data/reports/_grid_master.log", "a", encoding="utf-8", buffering=1)
master.write(f"\n########## 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} "
             f"pid={os.getpid()} ##########\n")

names = sys.argv[1:] or list(ABLATION_GRID)
for name in names:
    out = f"{OUT_DIR}/{name}.json"
    if os.path.exists(out):
        try:
            json.load(open(out, encoding="utf-8"))
            master.write(f"[skip] {name} 已有完整结果，跳过\n")
            continue
        except Exception:
            master.write(f"[warn] {name} 的 JSON 损坏，重跑\n")
    cmd = [sys.executable, "-u", "scripts/eval_runner.py", "--ablation", name,
           "--only", IDS, "--repeat", str(REPEAT),
           "--out", out, "--label", f"ablation:{name}"]
    master.write(f"\n[start] {name} {time.strftime('%H:%M:%S')} cfg={ABLATION_GRID[name]}\n")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(f"data/reports/_grid_{name}.log", "w", encoding="utf-8") as lg:
        rc = subprocess.call(cmd, stdout=lg, stderr=subprocess.STDOUT, env=env)
    master.write(f"[end]   {name} exit={rc} {time.strftime('%H:%M:%S')}\n")

master.write(f"ALL_DONE {time.strftime('%H:%M:%S')}\n")
