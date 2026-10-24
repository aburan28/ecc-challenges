#!/usr/bin/env python3
"""Reproduction gate for PROTOCOL-AMENDMENT-1.md and -2.md: the amended driver must
reproduce the stored output of the earlier runs (every field except the timing and the
mode label): A and A2 (mode 0), C (mode 2), the frozen B (mode 3), the
amendment-1 arm B2 (mode 4) and the amendment-2 arm B3 (mode 1). Writes results/reproduction_gate.json.

    python3 reproduction_gate.py --work WORK     (WORK holds solve_bench_new and jobs/)
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import run_study as plan  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--work", type=Path, required=True)
parser.add_argument("--binary", default="solve_bench")
parser.add_argument("--solves", type=int, default=5)
args = parser.parse_args()
rows = {r["id"]: r for r in json.loads((HERE.parents[1] / "tests/fixtures/toy_instances.json").read_text())}
checks = []
for instance in plan.PRIMARY + plan.SECONDARY:
    n = int(rows[instance]["n"], 16)
    for arm, mode in (("A", 0), ("B", 3), ("A2", 0), ("C", 2), ("B2", 4), ("B3", 1)):
        stored = HERE / "results" / "solve" / f"{instance}.{arm}.0000.jsonl"
        if not stored.exists():
            continue
        job = args.work / "jobs" / f"{instance}.{'v2' if mode == 0 else 'neg'}.job"
        seed = plan.ARMS[arm][2]
        out = subprocess.run([str(args.work / args.binary), "solve", str(mode), str(job), str(plan.LANES),
                              str(plan.ROUND_STEPS), str(plan.dp_bits(n, arm)), str(args.solves), str(seed), "0",
                              str(int(300 * plan.expected(n, "A")))], capture_output=True, text=True, check=True)
        old = stored.read_text().splitlines()[:args.solves]
        new = out.stdout.strip().splitlines()
        def strip(line):
            d = json.loads(line)
            d.pop("walk_ns"), d.pop("mode")
            return d
        same = [strip(a) == strip(b) for a, b in zip(new, old)]
        checks.append({"instance": instance, "frozen_arm": arm, "amended_mode": mode, "solves": len(same), "identical": all(same) and len(same) == args.solves})
result = {"pass": all(c["identical"] for c in checks), "checks": checks}
(HERE / "results" / "reproduction_gate.json").write_text(json.dumps(result, indent=1) + "\n")
print(json.dumps({"pass": result["pass"], "checks": len(checks)}))
