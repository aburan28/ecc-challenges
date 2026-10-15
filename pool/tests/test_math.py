import secrets

from eccpool.curvegen import make_challenge, point_order_in_hasse
from eccpool.ec import Curve
from eccpool.walk import WalkSpec, replay_segment, run_walk


def test_group_law(curve):
    G = curve.G
    assert curve.on_curve(G)
    assert curve.mul_raw(curve.n, G) is None
    a, b = secrets.randbelow(curve.n), secrets.randbelow(curve.n)
    assert curve.add(curve.mul(a, G), curve.mul(b, G)) == curve.mul(a + b, G)
    Q = curve.mul(12345, G)
    assert curve.mul2(a, b, Q) == curve.add(curve.mul(a, G), curve.mul(b, Q))
    assert curve.add(G, curve.neg(G)) is None


def test_order_counting_matches_bruteforce():
    # y^2 = x^3 + 2x + 3 over F_97: count points directly.
    p, a, b = 97, 2, 3
    count = 1 + sum(1 for x in range(p) for y in range(p)
                    if (y * y - x ** 3 - a * x - b) % p == 0)
    c = Curve(p, a, b, 0, 0, 1)
    for x in range(p):
        P = c.lift_x(x)
        if P is None:
            continue
        N = point_order_in_hasse(p, a, b, P)
        if N is not None:
            assert c.mul_raw(N, P) is None
            assert count % N == 0 or N == count


def test_challenge_is_deterministic(curve):
    one = make_challenge(curve, "seed-1")
    assert one == make_challenge(curve, "seed-1")
    assert one["Q"] != make_challenge(curve, "seed-2")["Q"]


def test_walk_is_reproducible_and_auditable(curve):
    Q = curve.mul(987654321, curve.G)
    spec = WalkSpec(curve, Q, "salt", dp_bits=8)
    res = run_walk(spec, "key1", "nonce1")
    assert res is not None
    assert res == run_walk(spec, "key1", "nonce1")
    assert spec.is_dp(res.dp)
    assert curve.mul2(res.a, res.b, Q) == res.dp
    assert len(res.checkpoints) == spec.n_checkpoints(res.length)
    _, _, start = spec.start("key1", "nonce1")
    anchors = [start] + res.checkpoints + [res.dp]
    for seg in range(spec.n_segments(res.length)):
        lo, hi = spec.segment_bounds(res.length, seg)
        last = seg == spec.n_segments(res.length) - 1
        end, err = replay_segment(spec, anchors[seg], hi - lo, last)
        assert err is None and end == anchors[seg + 1]


def test_start_is_bound_to_key(curve):
    Q = curve.mul(5, curve.G)
    spec = WalkSpec(curve, Q, "salt", dp_bits=8)
    assert spec.start("key1", "n")[2] != spec.start("key2", "n")[2]


def test_known_answer_vectors():
    """tests/vectors.json pins the walk spec; fast workers must match it too."""
    import json
    from pathlib import Path
    v = json.loads((Path(__file__).parent / "vectors.json").read_text())
    curve = Curve.from_dict(v["curve"])
    Q = (int(v["Q"][0], 16), int(v["Q"][1], 16))
    spec = WalkSpec(curve, Q, v["salt"], v["dp_bits"], v["r"], v["checkpoint_interval"])
    assert [{"c": hex(c), "d": hex(d), "R": [hex(R[0]), hex(R[1])]}
            for c, d, R in spec.multipliers] == v["multipliers"]
    for w in v["walks"]:
        a0, b0, start = spec.start(w["key_id"], w["nonce"])
        assert (hex(a0), hex(b0)) == (w["start"]["a0"], w["start"]["b0"])
        res = run_walk(spec, w["key_id"], w["nonce"])
        assert res.length == w["length"]
        assert [hex(res.dp[0]), hex(res.dp[1])] == w["dp"]
        assert (hex(res.a), hex(res.b)) == (w["a"], w["b"])
        assert [[hex(x), hex(y)] for x, y in res.checkpoints] == w["checkpoints"]
