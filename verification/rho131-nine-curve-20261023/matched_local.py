#!/usr/bin/env python3
"""Bounded, same-walk reference/fast131 invocation comparison on this host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "worker")]

from ecc_challenges.multicurve.codec import Namespace, WALK_V2  # noqa: E402
from generic import runtime  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("engine", type=Path)
    p.add_argument("--backend", choices=("cpu", "metal"), required=True)
    p.add_argument("--lanes", type=int, required=True)
    p.add_argument("--steps", type=int, required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.lanes <= 4096 or not 1 <= a.steps <= 4096 or not 1 <= a.repeats <= 8:
        p.error("outside bounded workload")
    a.out.mkdir(parents=True, exist_ok=False)
    ns = Namespace("ecc2-131", dp_bits=40, walk_version=WALK_V2)
    run_id = "18c3053b-96df-4879-a49d-18a52b8fdf8b"
    states = [runtime.NativeState(ns.start(run_id, i)) for i in range(a.lanes)]
    manifest = {
        "format": "rho131-local-matched-v1",
        "engine_sha256": sha(a.engine.read_bytes()),
        "source_sha256": {name: sha((ROOT / "worker/generic" / name).read_bytes())
                          for name in ("engine.cu", "ec_core.h", "fast131.h", "fast131_host.h")},
        "host": {key: value for key, value in platform.uname()._asdict().items()
                 if key != "node"},
        "backend": a.backend,
        "curve": "ecc2-131",
        "namespace_id": ns.id,
        "walk_version": WALK_V2,
        "dp_bits": 40,
        "run_id": run_id,
        "lanes": a.lanes,
        "steps_per_round": a.steps,
        "repeats": a.repeats,
        "timing_boundary": "Python run_round with one engine process invocation, native work, and output checks",
    }
    (a.out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    rows = []
    expected = None
    with (a.out / "rows.jsonl").open("x") as output:
        for repeat in range(a.repeats + 1):
            # Repeat zero warms the engine and driver and is excluded from ratios.
            order = ("reference", "fast131", "aa", "reference") if repeat % 2 == 0 else (
                "reference", "aa", "fast131", "reference")
            group = []
            for position, arm in enumerate(order):
                old = os.environ.get("ECC_ENGINE_FAST")
                try:
                    if arm == "fast131":
                        os.environ.pop("ECC_ENGINE_FAST", None)
                    else:
                        os.environ["ECC_ENGINE_FAST"] = "0"
                    started = time.perf_counter_ns()
                    result = runtime.run_round(a.engine, a.backend, ns, states, a.steps, timeout=300)
                    elapsed = time.perf_counter_ns() - started
                finally:
                    if old is None:
                        os.environ.pop("ECC_ENGINE_FAST", None)
                    else:
                        os.environ["ECC_ENGINE_FAST"] = old
                wanted_engine = "fast131" if arm == "fast131" else "reference"
                if result.receipt.get("engine") != wanted_engine:
                    raise RuntimeError(f"{arm}: unexpected engine {result.receipt.get('engine')}")
                state_bytes = b"".join(runtime.pack_state(s.record, s.active)
                                       for s in result.states)
                dp_bytes = b"".join(runtime.pack_state(r, 0)
                                    for r in result.distinguished)
                actual_steps = sum(s.steps - b.steps for s, b in zip(result.states, states))
                signature = (sha(state_bytes), sha(dp_bytes), actual_steps)
                if expected is None:
                    expected = signature
                elif signature != expected:
                    raise RuntimeError(f"{arm}: output differs from reference")
                row = {"repeat": repeat, "position": position, "arm": arm,
                       "elapsed_ns": elapsed, "actual_steps": actual_steps,
                       "state_sha256": signature[0], "dp_sha256": signature[1],
                       "engine_receipt": result.receipt}
                output.write(json.dumps(row, sort_keys=True) + "\n")
                output.flush()
                rows.append(row)
                group.append(row)
            if repeat:
                ends = [r["elapsed_ns"] for r in group if r["arm"] == "reference"]
                median = statistics.median(ends)
                comparison = {"repeat": repeat, "baseline_bracket_ns": ends,
                              "aa_over_bracket": next(r["elapsed_ns"] for r in group if r["arm"] == "aa") / median,
                              "fast_over_bracket": next(r["elapsed_ns"] for r in group if r["arm"] == "fast131") / median,
                              "actual_steps": group[0]["actual_steps"]}
                print(json.dumps(comparison, sort_keys=True), flush=True)
    comparisons = []
    for repeat in range(1, a.repeats + 1):
        group = [r for r in rows if r["repeat"] == repeat]
        ends = [r["elapsed_ns"] for r in group if r["arm"] == "reference"]
        median = statistics.median(ends)
        comparisons.append({"repeat": repeat,
                            "baseline_bracket_ns": ends,
                            "aa_over_bracket": next(r["elapsed_ns"] for r in group if r["arm"] == "aa") / median,
                            "fast_over_bracket": next(r["elapsed_ns"] for r in group if r["arm"] == "fast131") / median,
                            "actual_steps": group[0]["actual_steps"]})
    (a.out / "summary.json").write_text(json.dumps({"format": manifest["format"],
                                                     "comparisons": comparisons}, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
