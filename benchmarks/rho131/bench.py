#!/usr/bin/env python3
"""Steps per second of the 131-bit walk engines, one stage at a time.

Every row drives the engine the way worker/generic/client.py does: rounds of
`--steps` transitions for `--lanes` walks, validated in Python (run_round),
single-threaded unless --threads says otherwise. Stages differ only in the
engine switches and the walk, so a row's change is the stage's change:

  S0  reference engine (ECC_ENGINE_FAST=0), walk 2, process per round
  S1  specialised field arithmetic, batch 1, walk 2, process per round
  S3  + batch inversion (--batches)
  S4a + long-lived engine process (EngineSession)
  S4b + x-only partition and DP test (walk 3)
  S5  + negation map (walk 4)

Usage: bench.py ENGINE CAMPAIGN [--lanes 32 --steps 256 --rounds 3 ...]
Prints one JSON object per row on stdout and a table on stderr.
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "worker")]

from ecc_challenges.multicurve.codec import Namespace  # noqa: E402
from generic import runtime  # noqa: E402

RUN = "18c3053b-96df-4879-a49d-18a52b8fdf8b"


def measure(engine, ns, lanes, steps, rounds, env, session, backend="cpu"):
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        states = [runtime.NativeState(ns.start(RUN, i)) for i in range(lanes)]
        sess = runtime.EngineSession(engine, backend, ns, lanes) if session else None
        per_round = []  # (steps, wall seconds, engine cpu seconds)
        try:
            for r in range(rounds + 1):  # round 0 warms caches and is dropped
                t0, c0 = time.perf_counter(), sum(os.times()[2:4])
                res = runtime.run_round(engine, backend, ns, states, steps,
                                        timeout=3600, session=sess)
                wall, cpu = time.perf_counter() - t0, sum(os.times()[2:4]) - c0
                if r:
                    per_round.append((sum(s.steps - b.steps for s, b in zip(res.states, states)),
                                      wall, res.receipt.get("cpu_seconds", 0.0)))
                # restart finished walks like the client; keep the rest
                states = [s if s.active else runtime.NativeState(
                    ns.start(RUN, 10_000 * (r + 1) + i)) for i, s in enumerate(res.states)]
        finally:
            if sess:
                sess.close()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    wall = [n / w for n, w, _ in per_round]
    # Engine CPU seconds mean nothing for a GPU backend (the host thread waits).
    cpu = [n / c for n, _, c in per_round if c > 0 and backend == "cpu"]
    # Medians resist the other jobs on a shared host; the best round is the
    # least disturbed one and bounds what the engine can do.
    return {"median": statistics.median(wall), "best": max(wall),
            "cpu_median": statistics.median(cpu) if cpu else 0.0,
            "cpu_best": max(cpu) if cpu else 0.0}


def cpu_pick(row):
    return row["best_steps_per_engine_cpu_s"] or row["best_steps_per_s"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("campaign", choices=("ecc2-131", "eccp-131"))
    ap.add_argument("--lanes", type=int, default=32)
    ap.add_argument("--steps", type=int, default=256)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--batches", default="4,8,16,32,64")
    ap.add_argument("--skip-reference", action="store_true")
    ap.add_argument("--backend", default="cpu", choices=("cpu", "metal", "cuda"))
    ap.add_argument("--dp-bits", type=int, default=34)
    a = ap.parse_args()
    thr = {"OMP_NUM_THREADS": str(a.threads)}
    ns2, ns3, ns4 = (Namespace(a.campaign, a.dp_bits, v) for v in (2, 3, 4))
    batches = [int(b) for b in a.batches.split(",")]
    rows = []

    def row(stage, ns, env, session, label):
        m = measure(a.engine, ns, a.lanes, a.steps, a.rounds, {**thr, **env}, session, a.backend)
        rate, cpu_rate = m["median"], m["cpu_median"]
        rows.append({"stage": stage, "label": label, "walk": ns.walk_version,
                     "steps_per_s": round(rate), "best_steps_per_s": round(m["best"]),
                     "steps_per_engine_cpu_s": round(cpu_rate),
                     "best_steps_per_engine_cpu_s": round(m["cpu_best"]),
                     "backend": a.backend, "threads": a.threads, "lanes": a.lanes, "round_steps": a.steps,
                     "campaign": a.campaign})
        print(json.dumps(rows[-1]), flush=True)
        print(f"{stage:4} {label:34} {rate:11.0f} wall  {cpu_rate:11.0f} cpu-s (median)  {m['best_steps_per_engine_cpu_s']:11.0f} cpu-s (best)", file=sys.stderr, flush=True)
        return rate

    if not a.skip_reference:
        row("S0", ns2, {"ECC_ENGINE_FAST": "0"}, False, "reference engine, walk 2")
    if a.backend != "cpu":
        # GPU engines fix the batch per thread; there is no S1/S3 sweep.
        row("S1", ns2, {}, False, "fast field + batch inversion, walk 2")
        row("S4a", ns2, {}, True, "+ persistent engine")
        row("S4b", ns3, {}, True, "+ x-only partition/DP (walk 3)")
        row("S5", ns4, {}, True, "+ negation map (walk 4)")
        print(json.dumps({"meta": {"host": platform.platform(), "load": os.getloadavg(),
                                   "engine": a.engine}}), flush=True)
        return
    row("S1", ns2, {"ECC_F131_BATCH": "1"}, False, "fast field, batch 1, walk 2")
    best, best_rate = 1, 0
    for b in batches:
        r = row("S3", ns2, {"ECC_F131_BATCH": str(b)}, False, f"+ batch inversion {b}")
        if cpu_pick(rows[-1]) > best_rate:
            best, best_rate = b, cpu_pick(rows[-1])
    env = {"ECC_F131_BATCH": str(best)}
    row("S4a", ns2, env, True, f"+ persistent engine (batch {best})")
    row("S4b", ns3, env, True, "+ x-only partition/DP (walk 3)")
    row("S5", ns4, env, True, "+ negation map (walk 4)")
    meta = {"host": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "load": os.getloadavg(),
            "engine": a.engine}
    print(json.dumps({"meta": meta}), flush=True)


if __name__ == "__main__":
    main()
