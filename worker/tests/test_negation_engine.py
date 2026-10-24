"""Native engine checks for the negation-class walk (ECCJOB04) and the v2 guard.

The CPU engine must match the Python reference in
ecc_challenges.multicurve.negation_walk bit for bit on all 24 campaigns and on
toy curves long enough to exercise cycle escapes, reject malformed jobs and
states, and leave every v2 output unchanged. CUDA and Metal are not covered
here: this host has neither, and the negation walk is refused on Metal.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys

import pytest

from ecc_challenges.multicurve import negation_walk as nw
from ecc_challenges.multicurve.codec import WALK_NEGATION, Namespace, Record
from ecc_challenges.multicurve.instances import instance_ids
from generic.runtime import NativeState, pack_job, pack_state, run_round

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from toy_curves import load_toy_instances  # noqa: E402

RUN = "18c3053b-96df-4879-a49d-18a52b8fdf8b"
GOLDEN = json.loads((ROOT / "tests/fixtures/v2_golden.json").read_text())["engine"]
TOYS = load_toy_instances()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def toy_namespace(instance_id, dp_bits=40):
    instance, planted = TOYS[instance_id]
    return Namespace(instance.id, dp_bits, WALK_NEGATION, instance=instance), instance, planted


@pytest.mark.parametrize("campaign", instance_ids())
def test_v2_engine_output_is_byte_identical_to_the_pre_change_golden(engine, campaign):
    ns = Namespace(campaign, dp_bits=3)
    expected = GOLDEN[campaign]
    starts = tuple(NativeState(ns.start(RUN, t)) for t in range(3))
    job = pack_job(ns, 3, 12)
    assert job[:8] == b"ECCJOB03" and struct.unpack_from("<I", job, 8 + 7 * 4)[0] == 0
    out = run_round(engine, "cpu", ns, starts, 12, timeout=300)
    assert ns.id == expected["namespace_id"]
    assert sha(job) == expected["job_sha256"]
    assert sha(b"".join(pack_state(s.record, s.active) for s in starts)) == expected["start_sha256"]
    assert sha(b"".join(pack_state(s.record, s.active) for s in out.states)) == expected["state_sha256"]
    assert sha(b"".join(pack_state(NativeState(r).record, 0) for r in out.distinguished)) == expected["dp_sha256"]
    assert [s.steps for s in out.states] == expected["steps"]


@pytest.mark.parametrize("campaign", instance_ids())
def test_negation_engine_matches_reference_on_every_campaign(engine, campaign):
    ns = Namespace(campaign, dp_bits=3, walk=WALK_NEGATION)
    initial = tuple(NativeState(ns.start(RUN, t)) for t in range(3))
    job = pack_job(ns, 3, 12)
    assert job[:8] == b"ECCJOB04"
    result = run_round(engine, "cpu", ns, initial, 12, timeout=300)
    reference = nw.reference_round(ns, [(s.record, s.active) for s in initial], 12)
    assert [(s.record, s.active) for s in result.states] == reference
    for record in result.distinguished:
        ns.verify(record)


@pytest.mark.parametrize("instance_id", list(TOYS))
def test_negation_engine_matches_reference_through_cycle_escapes(engine, instance_id):
    ns, instance, _ = toy_namespace(instance_id)
    lanes = 8 if instance.field_kind == "prime" else 6
    steps = 3000
    initial = tuple(NativeState(ns.start(RUN, t)) for t in range(lanes))
    result = run_round(engine, "cpu", ns, initial, steps, timeout=300)
    counters = nw.Counters()
    reference = nw.reference_round(ns, [(s.record, s.active) for s in initial], steps, counters=counters)
    assert [(s.record, s.active) for s in result.states] == reference
    assert all(s.steps == steps and s.active for s in result.states)
    for s in result.states:
        assert instance.mul2(s.a, s.b) == s.point


def test_negation_engine_leaves_the_self_undoing_escape_trap(engine):
    """A trail start on which the escape without look-ahead repeated itself forever;
    the engine must match the reference through sixteen rounds and not be trapped."""
    from test_negation_walk import UNDOING
    ns, instance, _ = toy_namespace("toy-prime-35-3")
    record = Record(0, 0, UNDOING["point"], UNDOING["a"], UNDOING["b"])
    states = (NativeState(record),)
    reference = (record, 1)
    counters = nw.Counters()
    for _ in range(16):
        states = run_round(engine, "cpu", ns, states, 256, timeout=300).states
        reference = nw.walk_lane(ns, reference[0], reference[1], 256, counters=counters)
        assert (states[0].record, states[0].active) == reference
    assert counters.escapes < 10


def test_negation_engine_leaves_the_cycle_that_a_table_jump_escape_rejoins(engine):
    """The captured state of a lane on a 4-cycle (measurement solve 23, round 37)
    must match the reference and take only a few escapes."""
    from test_negation_walk import trapped_record
    ns, instance, _ = toy_namespace("toy-prime-35-3", dp_bits=11)
    record = trapped_record()
    counters = nw.Counters()
    reference = nw.walk_lane(ns, record, 1, 256, counters=counters)
    result = run_round(engine, "cpu", ns, (NativeState(record),), 256, timeout=300)
    assert (result.states[0].record, result.states[0].active) == reference
    assert counters.escapes <= 3


@pytest.mark.parametrize("instance_id", ["toy-prime-24-0", "toy-binary-25-0"])
def test_distinguished_points_verify_and_replay(engine, instance_id):
    ns, instance, _ = toy_namespace(instance_id, dp_bits=4)
    round_steps = 256
    states = tuple(NativeState(ns.start(RUN, t)) for t in range(8))
    starts = {s.trail_id: s.record for s in states}
    found = []
    for _ in range(40):
        result = run_round(engine, "cpu", ns, states, round_steps, timeout=300)
        found.extend(result.distinguished)
        states = tuple(s if s.active else NativeState(ns.start(RUN, s.trail_id + 100)) for s in result.states)
        starts.update({s.trail_id: s.record for s in states if s.steps == 0})
        if len(found) >= 6:
            break
    assert found
    for record in found:
        ns.verify(record)
        ns.check_trail(record, RUN)
        assert ns.distinguished(record.point) and ns.canonical_point(record.point)[1] is False
    # Replay: the reference regenerates a record from its trail start with the
    # same round size (the cycle window restarts every round).
    first = found[0]
    start = starts.get(first.trail_id) or ns.start(RUN, first.trail_id)
    replayed = nw.replay_to_distinguished(ns, start, round_steps, first.steps)
    assert replayed == first


def test_negation_engine_planted_solve(engine):
    ns, instance, planted = toy_namespace("toy-prime-24-0", dp_bits=4)
    instance = replace(instance, Q=instance.mul(planted, instance.G))
    ns = Namespace(instance.id, 4, WALK_NEGATION, instance=instance)
    lanes, trail = 8, 0
    states = []
    for _ in range(lanes):
        states.append(NativeState(ns.start(RUN, trail)))
        trail += 1
    table = {}
    for _ in range(400):
        result = run_round(engine, "cpu", ns, states, 128, timeout=300)
        states = []
        for s in result.states:
            if not s.active:
                ns.verify(s.record)
                key = s.record.point
                if key in table and table[key] != (s.a, s.b):
                    state, _, value, sign = ns.solve_collision(key, (table[key], (s.a, s.b)))
                    if state == "verified":
                        assert value == planted and sign == 1
                        return
                table.setdefault(key, (s.a, s.b))
                s = NativeState(ns.start(RUN, trail))
                trail += 1
            states.append(s)
    pytest.fail("no collision found")


# -- malformed input ----------------------------------------------------------------

def run(engine, tmp_path, job, states):
    (tmp_path / "job").write_bytes(job)
    (tmp_path / "in").write_bytes(states)
    return subprocess.run([str(engine), "--backend", "cpu", str(tmp_path / "job"), str(tmp_path / "in"),
                           str(tmp_path / "out"), str(tmp_path / "dp")], capture_output=True, text=True)


def test_job_magic_and_selector_must_agree(engine, tmp_path):
    ns = Namespace("eccp-79", dp_bits=3, walk=WALK_NEGATION)
    v2 = Namespace("eccp-79", dp_bits=3)
    state = pack_state(ns.start(RUN, 1))
    good = pack_job(ns, 1, 4)
    assert run(engine, tmp_path, good, state).returncode == 0
    # An ECCJOB04 header whose selector says "v2", and the reverse, are refused.
    zeroed = bytearray(good)
    struct.pack_into("<I", zeroed, 8 + 7 * 4, 0)
    assert "reserved" in run(engine, tmp_path, bytes(zeroed), state).stderr
    forged = bytearray(pack_job(v2, 1, 4))
    struct.pack_into("<I", forged, 8 + 7 * 4, 1)
    assert "reserved" in run(engine, tmp_path, bytes(forged), state).stderr
    # The measurement-only selectors (2 ablation, 3 and 4 earlier escape rules) are never accepted from a file.
    for selector in (2, 3, 4, 5):
        variant = bytearray(good)
        struct.pack_into("<I", variant, 8 + 7 * 4, selector)
        assert "reserved" in run(engine, tmp_path, bytes(variant), state).stderr
    wrong_magic = b"ECCJOB05" + good[8:]
    assert "magic" in run(engine, tmp_path, wrong_magic, state).stderr


def test_negation_engine_rejects_a_state_that_is_not_the_representative(engine, tmp_path):
    ns = Namespace("eccp-79", dp_bits=3, walk=WALK_NEGATION)
    instance = ns.instance
    start = ns.start(RUN, 1)
    negative = instance.neg(start.point)
    flipped = replace(start, point=negative, a=(-start.a) % instance.n, b=(-start.b) % instance.n)
    assert instance.mul2(flipped.a, flipped.b) == negative
    result = run(engine, tmp_path, pack_job(ns, 1, 4), pack_state(flipped))
    assert result.returncode != 0 and "canonical class representative" in result.stderr
    # The v2 engine accepts both members of the pair (it has no classes).
    v2 = Namespace("eccp-79", dp_bits=3)
    assert run(engine, tmp_path, pack_job(v2, 1, 4), pack_state(flipped)).returncode == 0


def test_python_runtime_refuses_non_representative_states():
    ns = Namespace("ecc2-79", dp_bits=3, walk=WALK_NEGATION)
    start = ns.start(RUN, 1)
    negative = ns.instance.neg(start.point)
    flipped = replace(start, point=negative, a=(-start.a) % ns.instance.n, b=(-start.b) % ns.instance.n)
    with pytest.raises(ValueError, match="canonical class representative"):
        run_round("/nonexistent", "cpu", ns, [flipped], 4)
