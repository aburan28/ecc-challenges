"""A complete solve through the production engine on a toy curve.

Walks 3 and 4 are run by the same engine, job format and record checks that
ECC2-131 and ECCp-131 use, on a group small enough to finish: DPs are
collected, two trails reach one stored point, and the scalar comes out of
(a1 - a2) / (b2 - b1). For walk 4 the colliding records may sit on opposite
sides of a +/- pair, which is exactly what the negation map must get right.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import subprocess
import sys

import pytest

from ecc_challenges.multicurve.codec import WALK_V3, WALK_V4, Namespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from toy_curve import N, SECRET, TOY, toy_namespaces  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "ecc_generic_runtime_solve", ROOT / "worker/generic/runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

RUN = "7b1d4c5e-3a8f-4f6e-9c52-0d1e2f3a4b5c"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.fail("a C++17 compiler is required")
    out = tmp_path_factory.mktemp("solve") / "engine"
    subprocess.run([compiler, "-std=c++17", "-O2", "-x", "c++",
                    str(ROOT / "worker/generic/engine.cu"), "-o", str(out)],
                   check=True, capture_output=True, text=True, timeout=900)
    return out


def solve(engine, walk, lanes=48, round_steps=64, max_steps=400_000, run=RUN):
    """Run until two records share a point with different witnesses.

    Returns (namespace, first record, second record, steps walked).
    """
    with toy_namespaces():
        ns = Namespace("toy-25", dp_bits=3, walk_version=walk)
        ns.dp_shift = 6  # the toy field has 25 bits; the production shift is 64
        states = [runtime.NativeState(ns.start(run, i)) for i in range(lanes)]
        next_trail, seen, total = lanes, {}, 0
        with runtime.EngineSession(engine, "cpu", ns, lanes) as session:
            while total < max_steps:
                result = runtime.run_round(engine, "cpu", ns, states, round_steps,
                                           session=session)
                # Count steps actually walked: a lane that reached a DP waits for the round to end.
                total += sum(after.steps - before.steps
                             for before, after in zip(states, result.states))
                for record in result.distinguished:
                    ns.verify(record)
                    prior = seen.setdefault(record.point, record)
                    if (prior.a, prior.b) != (record.a, record.b):
                        return ns, prior, record, total
                states = []
                for state in result.states:
                    if not state.active:
                        state = runtime.NativeState(ns.start(run, next_trail))
                        next_trail += 1
                    states.append(state)
    raise AssertionError("no collision within the step budget")


@pytest.mark.parametrize("walk", (WALK_V3, WALK_V4))
def test_walk_solves_the_toy_dlp_through_the_engine(engine, walk):
    ns, first, second, total = solve(engine, walk)
    assert first.point == second.point
    denominator = (second.b - first.b) % N
    if denominator == 0:
        pytest.skip("degenerate collision (same trail); the next seed would resolve it")
    d = (first.a - second.a) * pow(denominator, -1, N) % N
    assert d == SECRET and TOY.mul(d, TOY.G) == TOY.Q
    # the budget is ~100x the expected work; a stuck walk would exhaust it
    assert total < 100 * 4000


def test_negation_map_collides_across_the_pair(engine):
    """Some solve in a handful of seeds joins records of opposite parity b."""
    crossed = 0
    for seed in range(6):
        run = f"7b1d4c5e-3a8f-4f6e-9c52-0d1e2f3a4b{seed:02x}"
        ns, first, second, _ = solve(engine, WALK_V4, run=run)
        with toy_namespaces():
            ns_start = Namespace("toy-25", dp_bits=3, walk_version=WALK_V4)
            starts = [ns_start.start(run, t.trail_id).b for t in (first, second)]
        # b is a start's b or its negative: opposite-sign lanes met at one point
        signs = [(r.b == s) for r, s in zip((first, second), starts)]
        crossed += signs[0] != signs[1]
        d = (first.a - second.a) * pow((second.b - first.b) % N, -1, N) % N \
            if (second.b - first.b) % N else SECRET
        assert d == SECRET
    assert crossed, "no collision crossed the +/- pair in six solves"
