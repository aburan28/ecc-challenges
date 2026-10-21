"""The specialised 131-bit engine against the reference engine and Python.

Walk 2 is the published walk: the fast engine must reproduce the reference
engine (ECC_ENGINE_FAST=0) bit for bit. Walks 3 and 4 are new; their oracle is
tests/walk131_reference.py. Batch size, thread count, round chunking and a
long-lived session must never change an output.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import subprocess
import sys

import pytest

from ecc_challenges.multicurve.codec import (
    DEFAULT_WALKS, WALK_V2, WALK_V3, WALK_V4, Namespace, Record, namespace,
    pinned_namespace,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ecc_generic_runtime", ROOT / "worker/generic/runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)
sys.path.insert(0, str(ROOT / "tests"))
from walk131_reference import reference_round  # noqa: E402

# Namespace ids computed by origin/main (7f43c4d) before walk versions existed.
GOLDEN_V2_IDS = {'ecc2-131': '684a94e39489b40a4b99828ce331ca87a6fd781799b6d9a4feaef1398665b544', 'eccp-131': '1da43b4d6debab349c71187ecb8184e392a38d972ecf495fb05b0eb9d85da950', 'ecc2k-130': '5c6ced1c2c2015646d847cf06367e685c23b57b82075821196ecaef31840060c', 'eccp-79': '4f59c5763dec32ca6faccf6ad4ecc285122cd5367ab9a48c8b1f444df27e8b5a'}
RUN = "18c3053b-96df-4879-a49d-18a52b8fdf8b"
CURVES = ("ecc2-131", "eccp-131")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.fail("a C++17 compiler is required")
    out = tmp_path_factory.mktemp("fast131") / "engine"
    flags = ["-std=c++17", "-O2", "-x", "c++"]
    if subprocess.run([compiler, "-mpclmul", "-x", "c++", "-fsyntax-only", "-"],
                      input="", text=True, capture_output=True).returncode == 0:
        flags.append("-mpclmul")
    subprocess.run([compiler, *flags, str(ROOT / "worker/generic/engine.cu"),
                    "-o", str(out)], check=True, capture_output=True, text=True,
                   timeout=900)
    subprocess.run([str(out), "--self-test"], check=True, capture_output=True,
                   timeout=300)
    return out


def starts(ns, count):
    return [runtime.NativeState(ns.start(RUN, i)) for i in range(count)]


def same(left, right):
    assert left.states == right.states
    assert left.distinguished == right.distinguished


@pytest.mark.parametrize("campaign", CURVES + ("eccp-79", "eccp-109"))
def test_walk2_fast_engine_matches_reference_engine(engine, campaign, monkeypatch):
    ns = Namespace(campaign, dp_bits=3, walk_version=WALK_V2)
    initial = starts(ns, 9)
    monkeypatch.setenv("ECC_ENGINE_FAST", "0")
    expected = runtime.run_round(engine, "cpu", ns, initial, 40, timeout=900)
    assert expected.receipt["engine"] == "reference"
    monkeypatch.delenv("ECC_ENGINE_FAST")
    got = runtime.run_round(engine, "cpu", ns, initial, 40, timeout=900)
    assert got.receipt["engine"] == "fast131"
    same(got, expected)
    assert any(not s.active for s in got.states)  # DPs were exercised


@pytest.mark.parametrize("walk", (WALK_V3, WALK_V4))
@pytest.mark.parametrize("campaign", CURVES)
def test_new_walks_match_python_reference(engine, campaign, walk):
    ns = Namespace(campaign, dp_bits=5, walk_version=walk)
    initial = starts(ns, 6)
    got = runtime.run_round(engine, "cpu", ns, initial, 150, timeout=900)
    assert list(got.states) == reference_round(ns, initial, 150)
    assert got.distinguished
    for record in got.distinguished:
        ns.verify(record)  # aG + bQ = point, DP predicate, canonical sign


@pytest.mark.parametrize("campaign", CURVES)
def test_negation_map_keeps_exact_coefficients(engine, campaign):
    """Every state, not just DPs, must satisfy P = aG + bQ after negations."""
    ns = Namespace(campaign, dp_bits=40 - 1, walk_version=WALK_V4)
    res = runtime.run_round(engine, "cpu", ns, starts(ns, 5), 60, timeout=900)
    negated = 0
    for before, after in zip(starts(ns, 5), res.states):
        assert ns.instance.mul2(after.a, after.b) == after.point
        assert ns.canonical_sign(after.point)
        negated += after.b != before.b
    assert negated, "no lane was negated; the test would prove nothing"


@pytest.mark.parametrize("campaign", CURVES)
def test_output_independent_of_batch_threads_and_chunking(engine, campaign, monkeypatch):
    ns = Namespace(campaign, dp_bits=4, walk_version=WALK_V4)
    initial = starts(ns, 11)
    monkeypatch.setenv("ECC_F131_BATCH", "1")
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    single = runtime.run_round(engine, "cpu", ns, initial, 100, timeout=900)
    for batch, threads in (("5", "3"), ("64", "2"), ("32", "4")):
        monkeypatch.setenv("ECC_F131_BATCH", batch)
        monkeypatch.setenv("OMP_NUM_THREADS", threads)
        same(runtime.run_round(engine, "cpu", ns, initial, 100, timeout=900), single)
    # Splitting a round is exact for walks 2 and 3; for 4 only the cycle state
    # (reset per call) can differ, which the reference models, so compare it.
    half = runtime.run_round(engine, "cpu", ns, initial, 50, timeout=900)
    rest = runtime.run_round(engine, "cpu", ns, half.states, 50, timeout=900)
    assert list(rest.states) == reference_round(ns, half.states, 50)


@pytest.mark.parametrize("walk", (WALK_V2, WALK_V3, WALK_V4))
def test_long_lived_session_matches_one_shot_rounds(engine, walk):
    ns = Namespace("ecc2-131", dp_bits=4, walk_version=walk)
    state = starts(ns, 7)
    oneshot, resumed = list(state), list(state)
    with runtime.EngineSession(engine, "cpu", ns, len(state)) as session:
        for steps in (30, 45, 25):
            a = runtime.run_round(engine, "cpu", ns, oneshot, steps, timeout=900)
            b = runtime.run_round(engine, "cpu", ns, resumed, steps, timeout=900,
                                  session=session)
            same(a, b)
            oneshot, resumed = list(a.states), list(b.states)
            # restart finished lanes the way the client does
            oneshot = [s if s.active else runtime.NativeState(ns.start(RUN, 100 + i), 1)
                       for i, s in enumerate(oneshot)]
            resumed = list(oneshot)


def test_session_rejects_wrong_round_shape(engine):
    ns = Namespace("eccp-131", dp_bits=4)
    with runtime.EngineSession(engine, "cpu", ns, 3) as session:
        with pytest.raises(ValueError):
            runtime.run_round(engine, "cpu", ns, starts(ns, 2), 5, session=session)


def test_new_walks_are_refused_on_other_curves(engine):
    with pytest.raises(ValueError):
        Namespace("eccp-109", walk_version=WALK_V4)
    with pytest.raises(ValueError):
        Namespace("ecc2-131", walk_version=9)


def test_defaults_and_old_namespaces_stay_valid():
    for campaign in CURVES:
        assert namespace(campaign).walk_version == DEFAULT_WALKS[campaign] == WALK_V4
        old = namespace(campaign, walk_version=WALK_V2)
        new = namespace(campaign)
        assert old.id != new.id
        assert old.walk["id"] == "certicom-affine-radding32-v2"
        assert old.walk["native_job"] == "ECCJOB03"
        # A manifest pinned before walk versions keeps rebuilding to V2.
        assert pinned_namespace(campaign, old.public()).id == old.id
        assert pinned_namespace(campaign, new.public()).id == new.id
    assert namespace("ecc2k-130").walk_version == WALK_V2


def test_v2_namespace_ids_are_unchanged():
    """Golden ids from the repository before walk versions existed."""
    golden = GOLDEN_V2_IDS
    for campaign, identity in golden.items():
        assert namespace(campaign, walk_version=WALK_V2).id == identity, campaign


def test_v4_rejections():
    ns = Namespace("eccp-131", dp_bits=1, walk_version=WALK_V4)
    inst = ns.instance
    # Find a DP-like point of each sign by scanning multiples of G.
    found = {}
    k = 1
    while len(found) < 2 and k < 4000:
        p = inst.mul(k, inst.G)
        if ns.distinguished(p):
            found.setdefault(ns.canonical_sign(p), (k, p))
        k += 1
    assert len(found) == 2
    k, upper = found[False]
    with pytest.raises(ValueError, match="canonical"):
        ns.check_record(Record(1, 1, upper, k, 0))
    k, ok = found[True]
    ns.check_record(Record(1, 1, ok, k, 0))
    start = ns.start(RUN, 5)
    ns.check_trail(Record(5, 1, ok, 1, start.b), RUN)
    ns.check_trail(Record(5, 1, ok, 1, (inst.n - start.b) % inst.n), RUN)
    with pytest.raises(ValueError):
        ns.check_trail(Record(5, 1, ok, 1, (start.b + 1) % inst.n), RUN)
    v2 = Namespace("eccp-131", dp_bits=1, walk_version=WALK_V2)
    with pytest.raises(ValueError):
        v2.check_trail(Record(5, 1, ok, 1, (inst.n - start.b) % inst.n), RUN)
