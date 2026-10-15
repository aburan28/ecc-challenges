"""Regenerate tests/vectors.json: known-answer vectors for the walk spec.

A fast worker (C, CUDA, ...) is spec-compliant when it reproduces every
value here. Only regenerate when SPEC_VERSION changes.
"""

import json
from pathlib import Path

from eccpool.ec import Curve
from eccpool.walk import SPEC_VERSION, WalkSpec, run_walk

ROOT = Path(__file__).resolve().parent.parent


def build() -> dict:
    ch = json.loads((ROOT / "challenges" / "ecc32.json").read_text())
    curve = Curve.from_dict(ch["curve"])
    Q = curve.mul(0x1234567, curve.G)
    spec = WalkSpec(curve, Q, "vector-salt", dp_bits=10, r=32, checkpoint_interval=128)
    walks = []
    for nonce in ("n0", "n1", "n2"):
        a0, b0, start = spec.start("k1", nonce)
        res = run_walk(spec, "k1", nonce)
        walks.append({
            "key_id": "k1", "nonce": nonce,
            "start": {"a0": hex(a0), "b0": hex(b0), "point": [hex(start[0]), hex(start[1])]},
            "length": res.length, "dp": [hex(res.dp[0]), hex(res.dp[1])],
            "a": hex(res.a), "b": hex(res.b),
            "checkpoints": [[hex(x), hex(y)] for x, y in res.checkpoints],
        })
    return {
        "spec_version": SPEC_VERSION,
        "curve": curve.to_dict(), "Q": [hex(Q[0]), hex(Q[1])], "salt": "vector-salt",
        "dp_bits": 10, "r": 32, "checkpoint_interval": 128,
        "multipliers": [{"c": hex(c), "d": hex(d), "R": [hex(R[0]), hex(R[1])]}
                        for c, d, R in spec.multipliers],
        "walks": walks,
    }


if __name__ == "__main__":
    (ROOT / "tests" / "vectors.json").write_text(json.dumps(build(), indent=1) + "\n")
