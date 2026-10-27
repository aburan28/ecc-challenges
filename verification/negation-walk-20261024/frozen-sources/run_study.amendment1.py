#!/usr/bin/env python3
"""Run the measurement frozen in PROTOCOL.md (steps per solve and rate, v2 vs negation walk).

    python3 run_study.py build --work WORK
    python3 run_study.py solve --work WORK --budget-seconds 1500 [--procs 2]
    python3 run_study.py rate  --work WORK --budget-seconds 1500
    python3 run_study.py env   --work WORK

Every unit of work writes its own output file and is skipped when that file
exists, so the queue resumes after any interruption. Run each command under the
heavy-compute lock (flock). WORK holds the compiled driver and job files; the
results land in ./results/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO / "worker"), str(REPO / "tests")]

from toy_curves import load_toy_instances  # noqa: E402
from ecc_challenges.multicurve.codec import Namespace, WALK_NEGATION, WALK_V2  # noqa: E402
from generic.runtime import pack_job  # noqa: E402

# ---- frozen parameters (PROTOCOL.md section 4) --------------------------------------
LANES, ROUND_STEPS = 8, 256
SEED_MAIN, SEED_AA = 20261024, 20261025
CHUNK = 25
PRIMARY = ["toy-prime-24-0", "toy-prime-28-1", "toy-prime-31-2", "toy-prime-35-3"]
SECONDARY = ["toy-binary-25-0", "toy-binary-28-1", "toy-binary-33-2"]
POSITIVE = ["toy-prime-24-0", "toy-prime-28-1"]
N_PRIMARY, N_SECONDARY, N_POSITIVE = 300, 200, 100
RATE_REPS = 15
RATE_TOY = {"toy-prime-24-0": 16384, "toy-prime-35-3": 16384, "toy-binary-33-2": 16384}
RATE_REAL = {"eccp-79": 4096, "ecc2-79": 4096}
RATE_LANES = 16
# Arm B is the walk as first measured (frozen run); in the amended source that is
# mode 3 (escape step without look-ahead). B2 (PROTOCOL-AMENDMENT-1.md) is the
# amended walk, mode 1. In the frozen run's source B was mode 1.
ARMS = {"A": (0, WALK_V2, SEED_MAIN), "B": (3, WALK_NEGATION, SEED_MAIN),
        "A2": (0, WALK_V2, SEED_AA), "C": (2, WALK_NEGATION, SEED_MAIN),
        "B2": (1, WALK_NEGATION, SEED_MAIN)}


def expected(n: int, arm: str) -> float:
    return math.sqrt(math.pi * n / (2 if ARMS[arm][0] == 0 else 4))


def dp_bits(n: int, arm: str) -> int:
    return max(2, math.floor(math.log2(expected(n, arm))) - 6)


def plan():
    """(instance, arm, count) in frozen order."""
    rows = []
    for instance in PRIMARY:
        rows += [(instance, "A", N_PRIMARY), (instance, "B", N_PRIMARY), (instance, "A2", N_PRIMARY),
                 (instance, "B2", N_PRIMARY)]
    for instance in SECONDARY:
        rows += [(instance, "A", N_SECONDARY), (instance, "B", N_SECONDARY), (instance, "B2", N_SECONDARY)]
    for instance in POSITIVE:
        rows += [(instance, "C", N_POSITIVE)]
    return rows


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(work: Path):
    work.mkdir(parents=True, exist_ok=True)
    binary = work / "solve_bench"
    subprocess.run(["g++", "-std=c++17", "-O2", "-x", "c++",
                    str(REPO / "worker/generic/solve_bench.cpp"), "-o", str(binary)], check=True)
    jobs = work / "jobs"
    jobs.mkdir(exist_ok=True)
    toys = load_toy_instances()
    for instance_id, (instance, _planted) in toys.items():
        for walk, tag in ((WALK_V2, "v2"), (WALK_NEGATION, "neg")):
            ns = Namespace(instance.id, 10, walk, instance=instance)
            (jobs / f"{instance_id}.{tag}.job").write_bytes(pack_job(ns, 1, 1))
    for campaign in RATE_REAL:
        for walk, tag in ((WALK_V2, "v2"), (WALK_NEGATION, "neg")):
            ns = Namespace(campaign, 10, walk)
            (jobs / f"{campaign}.{tag}.job").write_bytes(pack_job(ns, 1, 1))
    print(json.dumps({"driver_sha256": sha(binary)}))


def job_file(work: Path, instance: str, mode: int) -> Path:
    return work / "jobs" / f"{instance}.{'v2' if mode == 0 else 'neg'}.job"


def run_chunk(work: Path, out: Path, instance: str, arm: str, first: int, count: int, n: int):
    target = out / "solve" / f"{instance}.{arm}.{first:04d}.jsonl"
    if target.exists():
        return target, 0.0
    mode, _walk, seed = ARMS[arm]
    limit = int(300 * expected(n, "A"))
    command = [str(work / "solve_bench"), "solve", str(mode), str(job_file(work, instance, mode)),
               str(LANES), str(ROUND_STEPS), str(dp_bits(n, arm)), str(count), str(seed), str(first), str(limit)]
    started = time.time()
    done = subprocess.run(command, capture_output=True, text=True, check=True)
    lines = done.stdout.strip().splitlines()
    if len(lines) != count:
        raise RuntimeError(f"{instance} {arm} {first}: {len(lines)} lines for {count} solves")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(done.stdout)
    os.replace(temporary, target)
    return target, time.time() - started


def solve(work: Path, out: Path, budget: float, procs: int):
    (out / "solve").mkdir(parents=True, exist_ok=True)
    toys = load_toy_instances()
    tasks = []
    chunks = max(math.ceil(c / CHUNK) for _, _, c in plan())
    for chunk in range(chunks):          # interleave arms and sizes in time
        for instance, arm, count in plan():
            first = chunk * CHUNK
            if first < count:
                tasks.append((instance, arm, first, min(CHUNK, count - first), int(toys[instance][0].n)))
    pending = [t for t in tasks
               if not (out / "solve" / f"{t[0]}.{t[1]}.{t[2]:04d}.jsonl").exists()]
    print(f"{len(tasks) - len(pending)} chunks done, {len(pending)} pending", flush=True)
    deadline = time.time() + budget
    def worker(task):
        if time.time() > deadline:
            return None
        return run_chunk(work, out, *task[:3], task[3], task[4])
    with ThreadPoolExecutor(procs) as pool:
        for task, result in zip(pending, pool.map(worker, pending)):
            if result is not None:
                print(f"{task[0]} {task[1]} {task[2]:04d} {result[1]:.1f}s", flush=True)
    left = [t for t in tasks if not (out / "solve" / f"{t[0]}.{t[1]}.{t[2]:04d}.jsonl").exists()]
    print(f"remaining chunks: {len(left)}", flush=True)


def rate(work: Path, out: Path, budget: float):
    (out / "rate").mkdir(parents=True, exist_ok=True)
    deadline = time.time() + budget
    curves = [(c, s) for c, s in list(RATE_TOY.items()) + list(RATE_REAL.items())]
    for instance, per_lane in curves:
        for rep in range(RATE_REPS):
            for arm_name, mode in (("A", 0), ("B2", 1)) if rep % 2 == 0 else (("B2", 1), ("A", 0)):
                target = out / "rate" / f"{instance}.{arm_name}.{rep:02d}.json"
                if target.exists():
                    continue
                if time.time() > deadline:
                    print("budget exhausted; rerun to resume", flush=True)
                    return
                command = [str(work / "solve_bench"), "rate", str(mode), str(job_file(work, instance, mode)),
                           str(RATE_LANES), str(ROUND_STEPS), str(per_lane * RATE_LANES), str(7 + rep)]
                text = subprocess.run(command, capture_output=True, text=True, check=True).stdout
                target.write_text(text)
        target = out / "rate" / f"{instance}.addtime.json"
        if not target.exists():
            runs = [subprocess.run([str(work / "solve_bench"), "addtime", str(job_file(work, instance, 0)),
                                    "100000"], capture_output=True, text=True, check=True).stdout.strip()
                    for _ in range(5)]
            target.write_text("[" + ",".join(runs) + "]\n")
    print("rate complete", flush=True)


def env(work: Path, out: Path):
    info = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "uname": platform.uname()._asdict(),
        "cpu": next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), None),
        "cores": os.cpu_count(),
        "compiler": subprocess.run(["g++", "--version"], capture_output=True, text=True).stdout.splitlines()[0],
        "flags": "g++ -std=c++17 -O2 -x c++ solve_bench.cpp (single thread, no OpenMP)",
        "driver_sha256": sha(work / "solve_bench"),
        "protocol_sha256": sha(HERE / "PROTOCOL.md"),
        "source_sha256": {p: sha(REPO / p) for p in (
            "worker/generic/ec_core.h", "worker/generic/engine.cu", "worker/generic/solve_bench.cpp",
            "src/ecc_challenges/multicurve/codec.py", "tests/fixtures/toy_instances.json")},
        "study_scripts_sha256": {p: sha(HERE / p) for p in ("run_study.py", "analyze.py")},
        "note": "L0 shared cloud VM, no hardware PMU, 4 vCPU shared by four agents; runs under heavy.lock",
    }
    (out / "env.json").write_text(json.dumps(info, indent=1, sort_keys=True) + "\n")
    print(json.dumps(info, indent=1, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "solve", "rate", "env"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--budget-seconds", type=float, default=1500)
    parser.add_argument("--procs", type=int, default=2)
    args = parser.parse_args()
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    if args.command == "build":
        build(args.work)
    elif args.command == "solve":
        solve(args.work, out, args.budget_seconds, args.procs)
    elif args.command == "rate":
        rate(args.work, out, args.budget_seconds)
    else:
        env(args.work, out)


if __name__ == "__main__":
    main()
