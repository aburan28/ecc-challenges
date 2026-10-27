"""A 25-bit prime curve of prime order, to run walks 3 and 4 to a real solve.

The 131-bit campaigns need about 2^65 steps; this group needs about 2^12, so a
whole solve (DPs, a collision between trails, the scalar from a1-a2 / b2-b1)
runs through the production engine in seconds. The curve was found by brute
force point counting: y^2 = x^3 + A x + B over P has prime order N.
"""
from __future__ import annotations

from contextlib import contextmanager

from ecc_challenges.multicurve import codec
from ecc_challenges.multicurve.curves import Instance

P = 16777259
A, B = 2939646, 5143990
N = 16782391
SECRET = 1234567


def _toy_instance():
    x = 1
    while True:
        f = (x ** 3 + A * x + B) % P
        y = pow(f, (P + 1) // 4, P)  # P = 3 mod 4
        if y * y % P == f:
            break
        x += 1
    g = (x, y)
    proto = Instance("toy-25", "prime", P.bit_length(), P, A, B, N, 1, g, g)
    q = proto.mul(SECRET, g)
    return Instance("toy-25", "prime", P.bit_length(), P, A, B, N, 1, g, q)


TOY = _toy_instance()


@contextmanager
def toy_namespaces():
    """Make codec.Namespace('toy-25', ...) build for the toy curve."""
    real_get, real_ids = codec.get_instance, codec.FAST_WALK_CAMPAIGNS
    codec.get_instance = lambda campaign: TOY if campaign == "toy-25" else real_get(campaign)
    codec.FAST_WALK_CAMPAIGNS = real_ids + ("toy-25",)
    try:
        yield
    finally:
        codec.get_instance, codec.FAST_WALK_CAMPAIGNS = real_get, real_ids
