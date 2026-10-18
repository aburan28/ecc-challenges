#!/usr/bin/env python3
"""Measure walk throughput of the generic engine's backends on this host.

Throughput is lane-steps per second: lanes x steps in one engine round,
divided by time. Three times are kept per round: wall (the whole engine
process, as the contributor client sees it), walk (the backend, from the
engine receipt) and, for Metal, kernel (walk minus shader compile and
pipeline setup). Configurations run interleaved, in a seeded order, so drift
on the host spreads over every arm.

Before any timing counts, each Metal configuration must reproduce the CPU
backend's output exactly on its first lanes; a mismatch aborts the run.
Starting states use the namespace's production DP threshold, so lanes almost
never stop early and every lane walks the full round.

Needs only Python and a built engine (deploy/native/install.sh or CMake):

  python3 scripts/bench_backends.py --engine ~/.local/ecc-challenges/bin/generic-engine \\
      --out verification/metal-throughput-YYYYMMDD
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
sys.path.insert(1, str(ROOT / "src"))

from ecc_challenges.multicurve.codec import Namespace  # noqa: E402
from generic.runtime import NativeState, run_round  # noqa: E402

RUN_ID = "18c3053b-96df-4879-a49d-18a52b8fdf8b"
PARITY_LANES = 64


def command(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30,
                              check=False).stdout.strip()
    except OSError:
        return ""


def host_info(engine: Path, probe: dict) -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "probe": probe,
            "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
            "source_commit": command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
            "source_dirty": bool(command("git", "-C", str(ROOT), "status", "--porcelain",
                                         "--untracked-files=no"))}
    if sys.platform == "darwin":
        for key in ("hw.model", "machdep.cpu.brand_string", "hw.ncpu",
                    "hw.perflevel0.physicalcpu", "hw.perflevel1.physicalcpu", "hw.memsize"):
            info[key] = command("sysctl", "-n", key)
        info["macos"] = command("sw_vers", "-productVersion")
        display = command("system_profiler", "SPDisplaysDataType", "-json")
        try:
            gpus = json.loads(display)["SPDisplaysDataType"]
            info["gpus"] = [{k: g.get(k) for k in ("sppci_model", "sppci_cores",
                                                    "sppci_vendor", "spdisplays_mtlgpufamilysupport")}
                            for g in gpus]
        except (ValueError, KeyError, TypeError):
            pass
        info["on_battery"] = "Battery Power" in command("pmset", "-g", "batt")
    return info


def states(ns: Namespace, lanes: int) -> tuple[NativeState, ...]:
    return tuple(NativeState(ns.start(RUN_ID, trail)) for trail in range(lanes))


def one_round(engine, backend, ns, lanes, steps, timeout):
    initial = states(ns, lanes)
    start = time.perf_counter()
    result = run_round(engine, backend, ns, initial, steps, timeout=timeout)
    wall = time.perf_counter() - start
    walk = float(result.receipt["walk_seconds"])
    setup = float(result.receipt.get("metal_setup_seconds", 0.0))
    stopped = sum(1 for s in result.states if not s.active)
    return result, {"wall_seconds": wall, "walk_seconds": walk,
                    "setup_seconds": setup, "kernel_seconds": walk - setup,
                    "stopped_lanes": stopped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--campaigns", default="eccp-131,ecc2-131,eccp-239,ecc2k-163")
    parser.add_argument("--metal-lanes", default="64,256,1024,4096")
    parser.add_argument("--cpu-lanes", default="",
                        help="default: the client's default, 4 x engine threads")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--target-seconds", type=float, default=2.0,
                        help="steps per round are chosen so a round's walk takes about this long")
    parser.add_argument("--max-steps", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    engine = args.engine.expanduser().resolve()
    probe = json.loads(subprocess.run([str(engine), "--probe"], check=True,
                                      capture_output=True, text=True).stdout)
    backends = probe["backends"]
    threads = int(probe["cpu_threads"])
    cpu_lanes = [int(x) for x in args.cpu_lanes.split(",") if x] or [min(4096, 4 * threads)]
    arms = [("cpu", n) for n in cpu_lanes]
    if "metal" in backends and probe.get("metal_devices"):
        arms += [("metal", int(x)) for x in args.metal_lanes.split(",") if x]
    campaigns = [c for c in args.campaigns.split(",") if c]
    args.out.mkdir(parents=True, exist_ok=True)
    host = host_info(engine, probe)
    print(json.dumps({"event": "start", "arms": arms, "campaigns": campaigns,
                      "host": host}), flush=True)

    # Parity first: Metal must reproduce the CPU walk lane for lane.
    parity = []
    for campaign in campaigns:
        ns = Namespace(campaign)
        reference, _ = one_round(engine, "cpu", ns, PARITY_LANES, 32, args.timeout)
        for backend, lanes in arms:
            if backend == "cpu":
                continue
            result, _ = one_round(engine, backend, ns, max(lanes, PARITY_LANES), 32,
                                  args.timeout)
            same = result.states[:PARITY_LANES] == reference.states
            parity.append({"campaign": campaign, "backend": backend, "lanes": lanes,
                           "compared_lanes": PARITY_LANES, "steps": 32, "identical": same})
            if not same:
                print(json.dumps({"event": "parity-failure", **parity[-1]}), flush=True)
                (args.out / "parity.json").write_text(json.dumps(parity, indent=1))
                return 1
    print(json.dumps({"event": "parity-passed", "checks": len(parity)}), flush=True)

    # Calibrate steps per arm so each round's walk takes about target_seconds.
    plan = []
    for campaign in campaigns:
        ns = Namespace(campaign)
        for backend, lanes in arms:
            _, probe_round = one_round(engine, backend, ns, lanes, 16, args.timeout)
            rate = lanes * 16 / max(probe_round["kernel_seconds"], 1e-6)
            steps = int(min(args.max_steps, max(16, args.target_seconds * rate / lanes)))
            steps -= steps % 16 or 0
            plan.append({"campaign": campaign, "backend": backend, "lanes": lanes,
                         "steps": max(16, steps)})
    print(json.dumps({"event": "plan", "plan": plan}), flush=True)

    rows = []
    order = random.Random(args.seed)
    for repeat in range(args.repeats):
        schedule = list(plan)
        order.shuffle(schedule)
        for arm in schedule:
            ns = Namespace(arm["campaign"])
            _, timing = one_round(engine, arm["backend"], ns, arm["lanes"], arm["steps"],
                                  args.timeout)
            work = arm["lanes"] * arm["steps"]
            row = {**arm, "repeat": repeat, **timing,
                   "steps_per_second_wall": work / timing["wall_seconds"],
                   "steps_per_second_walk": work / timing["walk_seconds"],
                   "steps_per_second_kernel": work / max(timing["kernel_seconds"], 1e-9)}
            rows.append(row)
            print(json.dumps({"event": "row", **row}), flush=True)

    summary = []
    for arm in plan:
        mine = [r for r in rows if all(r[k] == arm[k] for k in ("campaign", "backend", "lanes"))]
        entry = dict(arm, repeats=len(mine))
        for metric in ("wall", "walk", "kernel"):
            values = sorted(r[f"steps_per_second_{metric}"] for r in mine)
            entry[f"{metric}_median"] = statistics.median(values)
            entry[f"{metric}_min"], entry[f"{metric}_max"] = values[0], values[-1]
        entry["setup_seconds_median"] = statistics.median(r["setup_seconds"] for r in mine)
        summary.append(entry)
    report = {"format": "ecc-backend-throughput-v1",
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "command": sys.argv, "seed": args.seed, "repeats": args.repeats,
              "host": host, "parity": parity, "plan": plan, "summary": summary,
              "rows": rows, "omp_num_threads": os.environ.get("OMP_NUM_THREADS")}
    (args.out / "results.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({"event": "done", "summary": summary}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
