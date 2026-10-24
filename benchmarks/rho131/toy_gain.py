#!/usr/bin/env python3
"""Mean steps to a collision on the 25-bit toy curve, walk 3 against walk 4.

The negation map should cut the expected work by sqrt(2). This measures it
through the production engine (tests/toy_curve.py has the curve), including
the cost of fruitless cycles and their escape. Prints one JSON object.

Usage: toy_gain.py ENGINE [--trials 300]
"""
import argparse
import json
import math
import statistics
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "worker"), str(ROOT / "tests")]

import test_fast131_solve as solver  # noqa: E402
from toy_curve import N  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--lanes", type=int, default=16)
    ap.add_argument("--round-steps", type=int, default=8)
    a = ap.parse_args()
    out = {"group_order": N, "trials": a.trials,
           "theory_walk3": math.sqrt(math.pi * N / 2), "theory_walk4": math.sqrt(math.pi * N / 4)}
    for walk in (3, 4):
        totals = []
        for _ in range(a.trials):
            *_, total = solver.solve(Path(a.engine), walk, lanes=a.lanes,
                                     round_steps=a.round_steps, run=str(uuid.uuid4()),
                                     max_steps=5_000_000)
            totals.append(total)
        mean = statistics.mean(totals)
        out[f"walk{walk}_mean_steps"] = round(mean)
        out[f"walk{walk}_stderr"] = round(statistics.stdev(totals) / math.sqrt(len(totals)))
    out["measured_ratio_walk3_over_walk4"] = round(out["walk3_mean_steps"] / out["walk4_mean_steps"], 3)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
