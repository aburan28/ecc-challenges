"""The negation-class walk: classes, coefficient signs, collisions, cycles,
namespaces and the regression guards for the v1 and v2 walks.

The native engine is checked against the reference in
worker/tests/test_negation_engine.py; everything here runs on the Python
reference only (toy curves from tests/fixtures/toy_instances.json).
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random

import pytest

from ecc_challenges.multicurve import negation_walk as nw
from ecc_challenges.multicurve.codec import (
    NEGATION_PARAMETERS, WALK_IDS, WALK_NEGATION, WALK_V2, Namespace, Record, canonical,
    dp_bits_for, expected_steps_log2, namespace, pinned_namespace)
from ecc_challenges.multicurve.instances import all_instances
from toy_curves import load_toy_instances

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
RUN = "18c3053b-96df-4879-a49d-18a52b8fdf8b"
TOYS = load_toy_instances()
TOY_IDS = list(TOYS)


def toy(instance_id, dp_bits=3, walk=WALK_NEGATION):
    instance, planted = TOYS[instance_id]
    return Namespace(instance.id, dp_bits, walk, instance=instance), instance, planted


def random_point(instance, rng):
    return instance.mul(rng.randrange(1, instance.n), instance.G)


# -- regression guards: v1 and v2 must not move --------------------------------

def test_v2_namespaces_unchanged():
    golden = json.loads((FIXTURES / "v2_golden.json").read_text())["namespaces"]
    assert len(golden) == 24
    for instance in all_instances():
        ns = Namespace(instance.id, walk_version=WALK_V2)
        expected = golden[instance.id]
        assert ns.id == expected["namespace_id"]
        assert ns.dp_bits == expected["dp_bits"] == dp_bits_for(instance.n)
        assert hashlib.sha256(canonical(ns.public())).hexdigest() == expected["public_sha256"]
        assert ns.record_bytes == expected["record_bytes"]
        assert ns.walk["id"] == WALK_IDS[WALK_V2] and ns.walk["native_job"] == "ECCJOB03"
        assert not ns.negation


def test_v1_walk_sources_unchanged():
    """The production ECC2K-130 v1 walk is exactly the committed bytes."""
    pinned = json.loads((FIXTURES / "v1_digests.json").read_text())
    for name, digest in pinned.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, (
            f"{name} changed. The ECC2K-130 v1 walk must stay byte for byte unchanged; if a change "
            "is intended, update tests/fixtures/v1_digests.json in a PR that says so")
    from ecc_challenges import protocol
    assert (protocol.CAMPAIGN, protocol.WALK_ID, protocol.RECORD_BYTES, protocol.DP_WEIGHTS) == (
        "ecc2k-130", "certicom-ecc2k130-frobenius-v1", 32, (30, 32, 34))


def test_negation_namespace_is_separate_and_v2_resolution_is_unchanged():
    for campaign in ("eccp-79", "ecc2-131", "ecc2k-130"):
        v2, negation = namespace(campaign, None, WALK_V2), namespace(campaign, None, WALK_NEGATION)
        assert v2.id != negation.id
        assert negation.walk["id"] == WALK_NEGATION and negation.walk["native_job"] == "ECCJOB04"
        assert negation.walk["automorphism_quotient"] == "negation"
        assert negation.record_bytes == v2.record_bytes
        assert pinned_namespace(campaign, negation.public()).id == negation.id
        assert pinned_namespace(campaign, v2.public()).id == v2.id
        assert negation.walk["cycle_handling"]["cycle_window"] == NEGATION_PARAMETERS["cycle_window"]
    with pytest.raises(ValueError):
        pinned_namespace("eccp-79", {"walk": {"id": "other", "dp_bits": 3}})


def test_dp_bits_for_negation_uses_half_the_classes():
    for instance in all_instances():
        gap = expected_steps_log2(instance.n) - expected_steps_log2(instance.n, WALK_NEGATION)
        assert gap == pytest.approx(0.5)
        assert dp_bits_for(instance.n, WALK_NEGATION) in (dp_bits_for(instance.n), dp_bits_for(instance.n) - 1) \
            or dp_bits_for(instance.n) == 34


# -- classes, canonical encoding, coefficients -------------------------------------

@pytest.mark.parametrize("instance_id", TOY_IDS)
def test_class_functions_are_functions_of_the_class(instance_id):
    ns, instance, _ = toy(instance_id, dp_bits=1)
    rng = random.Random(1)
    for _ in range(40):
        p = random_point(instance, rng)
        q = instance.neg(p)
        assert ns.canonical_point(p)[0] == ns.canonical_point(q)[0]
        assert ns.canonical_point(p)[1] != ns.canonical_point(q)[1] or p == q
        assert ns.selector(p) == ns.selector(q)
        assert ns.partition(p) == ns.partition(q)
        assert ns.distinguished(p) == ns.distinguished(q)
        rep = ns.canonical_point(p)[0]
        assert rep[1] <= instance.neg(rep)[1]
    assert ns.canonical_point(None) == (None, False)


def test_class_functions_on_real_campaigns():
    for campaign in ("eccp-79", "ecc2-79", "ecc2k-130", "eccp-359", "ecc2-353"):
        ns = namespace(campaign, 1, WALK_NEGATION)
        p = ns.instance.mul(12345, ns.instance.G)
        q = ns.instance.neg(p)
        assert ns.selector(p) == ns.selector(q)
        assert ns.canonical_point(p)[0] == ns.canonical_point(q)[0]
        # The v2 namespace is not class-aware: it hashes whichever point it is given.
        assert namespace(campaign, 1).selector(p) != namespace(campaign, 1).selector(q)


@pytest.mark.parametrize("instance_id", TOY_IDS)
def test_start_is_canonical_and_keeps_the_relation(instance_id):
    ns, instance, _ = toy(instance_id)
    for trail in range(30):
        start = ns.start(RUN, trail)
        assert ns.canonical_point(start.point)[1] is False
        assert instance.mul2(start.a, start.b) == start.point
        ns.check_trail(replace(start, steps=5), RUN)


@pytest.mark.parametrize("instance_id", TOY_IDS)
def test_check_trail_accepts_b_or_minus_b_only(instance_id):
    ns, instance, _ = toy(instance_id)
    start = ns.start(RUN, 7)
    n = instance.n
    ns.check_trail(replace(start, b=start.b), RUN)
    ns.check_trail(replace(start, b=(-start.b) % n), RUN)
    for wrong in (1, 2 * start.b % n, (start.b + 1) % n):
        if wrong not in (start.b, (-start.b) % n):
            with pytest.raises(ValueError):
                ns.check_trail(replace(start, b=wrong), RUN)
    v2 = Namespace(instance.id, 3, WALK_V2, instance=instance)
    with pytest.raises(ValueError):
        v2.check_trail(replace(v2.start(RUN, 7), b=(-v2.start(RUN, 7).b) % n), RUN)


@pytest.mark.parametrize("instance_id", TOY_IDS)
def test_walk_keeps_relation_and_canonical_state(instance_id):
    ns, instance, _ = toy(instance_id, dp_bits=40)
    for trail in range(3):
        record, active = nw.walk_lane(ns, ns.start(RUN, trail), 1, 600)
        assert active == 1 and record.steps == 600
        assert instance.mul2(record.a, record.b) == record.point
        assert ns.canonical_point(record.point)[1] is False
        ns.check_trail(record, RUN)


# -- the DP record and its verification --------------------------------------------

@pytest.mark.parametrize("instance_id", ["toy-prime-24-0", "toy-binary-25-0"])
def test_distinguished_record_verification_and_rejections(instance_id):
    ns, instance, _ = toy(instance_id, dp_bits=3)
    found = None
    for trail in range(200):
        record, active = nw.walk_lane(ns, ns.start(RUN, trail), 1, 400)
        if not active:
            found = record
            break
    assert found is not None
    ns.verify(found)
    ns.check_trail(found, RUN)
    assert ns.decode(ns.encode(found)) == [found]
    # The negative of the same class member is the same class but not the stored form.
    other = instance.neg(found.point)
    if other != found.point:
        flipped = replace(found, point=other, a=(-found.a) % instance.n, b=(-found.b) % instance.n)
        assert instance.mul2(flipped.a, flipped.b) == flipped.point
        with pytest.raises(ValueError, match="canonical"):
            ns.verify(flipped)
        with pytest.raises(ValueError, match="canonical"):
            ns.encode(flipped)
    with pytest.raises(ValueError):
        ns.verify(replace(found, a=(found.a + 1) % instance.n))
    with pytest.raises(ValueError):
        ns.verify(replace(found, b=(-found.b) % instance.n))
    with pytest.raises(ValueError):
        ns.check_trail(replace(found, b=(found.b + 1) % instance.n), RUN)
    # Replaying the trail from its start reproduces the record (same round size).
    replayed = nw.replay_to_distinguished(ns, ns.start(RUN, found.trail_id), 400, 400)
    assert replayed == found


# -- collisions and the sign ---------------------------------------------------------

@pytest.mark.parametrize("instance_id", ["toy-prime-24-0", "toy-binary-25-0", "toy-prime-35-3"])
def test_collision_solve_handles_both_signs(instance_id):
    ns, instance, d = toy(instance_id)
    n, rng = instance.n, random.Random(5)
    a2, b2 = rng.randrange(n), rng.randrange(1, n)
    r2 = instance.mul2(a2, b2)
    b1 = (b2 + rng.randrange(1, n - 1)) % n
    # Sign +: the same point written with different coefficients.
    a1 = (a2 + (b2 - b1) * d) % n
    assert instance.mul2(a1, b1) == r2
    state, _, value, sign = ns.solve_collision(r2, ((a1, b1), (a2, b2)))
    assert (state, value, sign) == ("verified", d, 1)
    # Sign -: the other record holds the negative point, R1 = -R2.
    a1n = (-a2 - (b1 + b2) * d) % n
    assert instance.mul2(a1n, b1) == instance.neg(r2)
    value_plus = (a1n - a2) * pow(b2 - b1, -1, n) % n
    assert instance.mul(value_plus, instance.G) != instance.Q    # assuming + would be wrong
    state, _, value, sign = ns.solve_class_collision(
        (instance.neg(r2), a1n, b1), (r2, a2, b2))
    assert (state, value, sign) == ("verified", d, -1)
    # The v2 namespace has one candidate only and refuses points in different classes.
    v2 = Namespace(instance.id, 3, WALK_V2, instance=instance)
    assert v2.solve_collision(r2, ((a1, b1), (a2, b2)))[3] == 1
    with pytest.raises(ValueError):
        v2.solve_class_collision((instance.neg(r2), a1n, b1), (r2, a2, b2))
    with pytest.raises(ValueError):
        ns.solve_class_collision((instance.mul(3, instance.G), 3, 0), (r2, a2, b2))
    # Degenerate: equal b gives no information.
    assert ns.solve_collision(r2, ((a2, b2), (a2 + 1, b2)))[0] == "degenerate"
    # A wrong scalar claim is rejected, not accepted.
    assert ns.solve_collision(r2, ((a1 + 1, b1), (a2, b2)))[0] == "rejected"


def run_planted_solve(ns, instance, planted, seed, lanes=4, round_steps=64, limit=200000):
    """Planted solve on the reference walk: DP table, collision, sign-checked solve."""
    rng = random.Random(seed)
    toy_instance = replace(instance, Q=instance.mul(planted, instance.G))
    ns = Namespace(toy_instance.id, ns.dp_bits, ns.walk["id"], instance=toy_instance)
    next_trail = 0
    state = []
    for _ in range(lanes):
        state.append((ns.start(RUN + "", next_trail + seed * 1000), 1))
        next_trail += 1
    table, steps = {}, 0
    while steps < limit:
        for lane, (record, active) in enumerate(state):
            before = record.steps
            record, active = nw.walk_lane(ns, record, active, round_steps) if ns.negation else \
                plain_round(ns, record, active, round_steps)
            steps += record.steps - before
            if not active:
                key = record.point
                if key in table and table[key][:2] != (record.a, record.b):
                    first = table[key]
                    result = ns.solve_collision(key, ((first[0], first[1]), (record.a, record.b)))
                    if result[0] == "verified":
                        return result[2], steps
                table.setdefault(key, (record.a, record.b))
                record = ns.start(RUN, next_trail + seed * 1000)
                next_trail += 1
                active = 1
            state[lane] = (record, active)
    raise AssertionError("no collision within the step limit")


def plain_round(ns, record, active, steps):
    for _ in range(steps):
        delta, da, db = ns.table()[ns.partition(record.point)]
        point = ns.instance.add(record.point, delta)
        record = Record(record.trail_id, record.steps + 1, point,
                        (record.a + da) % ns.instance.n, (record.b + db) % ns.instance.n)
        if ns.distinguished(point):
            return record, 0
    return record, active


@pytest.mark.parametrize("instance_id", ["toy-prime-24-0", "toy-binary-25-0"])
@pytest.mark.parametrize("walk", [WALK_NEGATION, WALK_V2])
def test_planted_solves_recover_the_scalar(instance_id, walk):
    ns, instance, _ = toy(instance_id, dp_bits=3, walk=walk)
    for seed, planted in enumerate((0x1234567 % instance.n, 0x7654321 % instance.n, 99991), 1):
        value, steps = run_planted_solve(ns, instance, planted, seed)
        assert value == planted
        assert steps > 0


# -- fruitless cycles --------------------------------------------------------------------

def class_sequence(ns, trail, count, handling):
    record, digest = ns.start(RUN, trail), None
    counters = nw.Counters()
    digest = ns.selector(record.point)
    index = digest[0] & 31
    classes = [record.point]
    for _ in range(count):
        record, digest = nw._step(ns, record, index, nw.ATTEMPTS if handling else 1, counters)
        index = digest[0] & 31
        classes.append(record.point)
    return classes, counters


def test_look_ahead_removes_two_cycles():
    ns, instance, _ = toy("toy-prime-28-1", dp_bits=40)
    plain_returns = handled_returns = 0
    for trail in range(12):
        plain, _ = class_sequence(ns, trail, 2000, handling=False)
        handled, counters = class_sequence(ns, trail, 2000, handling=True)
        plain_returns += sum(plain[i] == plain[i + 2] for i in range(len(plain) - 2))
        handled_returns += sum(handled[i] == handled[i + 2] for i in range(len(handled) - 2))
        assert counters.lookaheads / counters.point_adds < 0.05
    # Without the look-ahead the walk gets trapped in 2-cycles; with it, none remain.
    assert plain_returns > 100
    assert handled_returns == 0


def test_escape_jump_avoids_every_jump_a_cycle_can_use():
    # A 4-cycle alternating partitions 4 and 20 (a fixed offset of 16 would pick
    # jump 20 and fall straight back in; found on a toy binary curve).
    members = [4, 20, 4, 20]
    jump = nw.escape_jump(members, 4)
    used = {(m + k) & 31 for m in members for k in range(nw.ATTEMPTS)}
    assert jump not in used and jump == 24
    # Always a function of the cycle: the same members and index, the same jump.
    assert nw.escape_jump(list(members), 4) == jump
    # If every jump may have been used there is nothing to avoid: the fixed offset.
    assert nw.escape_jump(list(range(0, 32, 4)), 5) == (5 + nw.ESCAPE_OFFSET) & 31


def test_walk_leaves_cycles_and_plain_walk_does_not():
    ns, instance, _ = toy("toy-prime-24-0", dp_bits=40)
    handled, plain = nw.Counters(), nw.Counters()
    for trail in range(12):
        record, active = nw.walk_lane(ns, ns.start(RUN, trail), 1, 3000, counters=handled)
        assert active and record.steps == 3000
        assert instance.mul2(record.a, record.b) == record.point       # the escape keeps R = aG + bQ
    assert handled.escapes >= 1
    # The ablation (no look-ahead, no cycle check) never escapes anything.
    for trail in range(12):
        nw.walk_lane(ns, ns.start(RUN, trail), 1, 3000, mode=2, counters=plain)
    assert plain.lookaheads == 0 and plain.escapes == 0


def test_round_budget_is_exact_even_with_escapes():
    ns, instance, _ = toy("toy-prime-24-0", dp_bits=40)
    for steps in (1, 3, 4, 5, 33, 257, 1000):
        for trail in range(4):
            record, active = nw.walk_lane(ns, ns.start(RUN, trail), 1, steps)
            assert active and record.steps == steps


# A trail start captured from the measurement (toy-prime-35-3): the table-jump escape without the
# 2-cycle look-ahead (mode 3) undoes itself on it and repeats forever. States are pinned as numbers
# so these tests do not move when the namespace manifest text changes.
UNDOING = {"point": (17023243534, 584948543), "a": 0x10688a060, "b": 0xbab98972}


def test_escape_step_has_the_look_ahead_so_it_cannot_undo_itself():
    """The cycle's deterministic table-jump escape lands on a point whose own step
    is the same jump with the sign flipped, i.e. straight back (an escape that
    undoes itself). Without the look-ahead on the escape step (mode 3) the walk
    repeats the escape for the rest of the trail; with it (mode 4) or with the
    c*G escape (mode 1) the lane leaves."""
    ns, instance, _ = toy("toy-prime-35-3", dp_bits=40)
    assert instance.on_curve(UNDOING["point"])

    def escapes(mode):
        record = Record(0, 0, UNDOING["point"], UNDOING["a"], UNDOING["b"])
        active, counters = 1, nw.Counters()
        for _ in range(16):
            record, active = nw.walk_lane(ns, record, active, 256, mode=mode, counters=counters)
        return counters.escapes
    assert escapes(3) > 300        # the walk as first measured: trapped for the rest of the trail
    assert escapes(4) < 10 and escapes(1) < 10


# A lane state captured from the measurement (toy-prime-35-3, solve 23, 8 lanes, round 37): on a
# 4-cycle between partitions 17 and 26. An escape by a table jump (mode 4) rejoins the cycle after
# two steps (M -> E -> E2 -> M) and repeats forever; the c*G escape (mode 1) leaves for good.
TRAPPED = {"point": (0x56061c133, 0xc9f3f843), "a": 0x3c6980c1, "b": 0x277f52bf5, "steps": 512}


def trapped_record():
    return Record(0, TRAPPED["steps"], TRAPPED["point"], TRAPPED["a"], TRAPPED["b"])


def test_table_jump_escape_rejoins_the_cycle_and_small_multiple_escape_does_not():
    ns, instance, _ = toy("toy-prime-35-3", dp_bits=11)
    assert instance.on_curve(TRAPPED["point"]) and ns.canonical_point(TRAPPED["point"])[1] is False
    counts = {}
    for mode in (4, 1):
        counters = nw.Counters()
        record, active = nw.walk_lane(ns, trapped_record(), 1, 256, mode=mode, counters=counters)
        assert active and record.steps == 768
        counts[mode] = counters.escapes
    assert counts[4] >= 15
    assert counts[1] <= 3


def test_small_multiple_escape_keeps_b_and_the_relation():
    ns, instance, _ = toy("toy-prime-35-3", dp_bits=40)
    n = instance.n
    point = instance.mul(0x2468ace13579, instance.G)
    a0, b0 = 0x2468ace13579 % n, 0
    record = Record(1, 7, ns.canonical_point(point)[0], a0, b0)
    if ns.canonical_point(point)[1]:
        record = replace(record, a=(-a0) % n)
    counters = nw.Counters()
    for smallest in (1 << 5, 123456789 << 5 | 7, (1 << 64) - 1):
        moved, digest = nw._escape_small(ns, record, smallest, counters)
        c = nw.escape_multiplier(smallest)
        assert 1 <= c <= 65535
        # R = aG + 0*Q exactly, b unchanged up to sign, and the point is the representative.
        assert instance.mul2(moved.a, moved.b) == moved.point
        assert moved.b in (0, (-0) % n) and moved.steps == 8
        assert ns.canonical_point(moved.point)[1] is False
        assert digest == ns.selector(moved.point)
