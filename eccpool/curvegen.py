"""Generate prime-order curves and nothing-up-my-sleeve challenges.

Point counting uses baby-step giant-step inside the Hasse interval, which
costs O(p^(1/4)) group operations: fine up to p of about 72 bits in pure
Python, and the range where rho challenges are solvable anyway. Prime order
N > 4*sqrt(p) with N*P = O for a point P != O proves #E = N.

G and Q are hash-derived from a public seed (try-and-increment), so nobody
knows log_G(Q): the challenge is honest by construction.
"""

from __future__ import annotations

import hashlib
import math
import secrets

from .ec import Curve, is_probable_prime


def _random_prime(bits: int, rng: secrets.SystemRandom) -> int:
    while True:
        p = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if p % 4 == 3 and is_probable_prime(p):
            return p


def point_order_in_hasse(curve_p: int, a: int, b: int, P: tuple[int, int]) -> int | None:
    """The unique N in the Hasse interval with N*P = O, or None if not unique."""
    tmp = Curve(curve_p, a, b, P[0], P[1], 1)
    lo = curve_p + 1 - 2 * math.isqrt(curve_p) - 2
    width = 4 * math.isqrt(curve_p) + 5
    m = math.isqrt(width) + 1
    baby: dict[int, list[int]] = {}
    R = None
    for j in range(m + 1):
        if R is not None:
            baby.setdefault(R[0], []).append(j)
        R = tmp.add(R, P)
    # N = lo + i*m - j for some i, j: look for (lo + i*m)P == j*P (same x, same y).
    mP = tmp.mul_raw(m, P)
    S = tmp.mul_raw(lo, P)
    found: set[int] = set()
    for i in range(m + 2):
        if S is None:
            found.add(lo + i * m)
        elif S[0] in baby:
            for j in baby[S[0]]:
                Jp = tmp.mul_raw(j, P)
                if Jp == S:
                    found.add(lo + i * m - j)
                elif Jp is not None and Jp[1] == (-S[1]) % curve_p:
                    found.add(lo + i * m + j)
        S = tmp.add(S, mP)
    lo_h = curve_p + 1 - 2 * math.isqrt(curve_p) - 1
    hi_h = curve_p + 1 + 2 * math.isqrt(curve_p) + 1
    found = {N for N in found if lo_h <= N <= hi_h}
    return found.pop() if len(found) == 1 else None


def hash_to_point(p: int, a: int, b: int, n: int, label: str, seed: str) -> tuple[int, int]:
    c = Curve(p, a, b, 0, 0, n)
    ctr = 0
    while True:
        digest = hashlib.sha512(f"eccpool:{label}:{seed}:{ctr}".encode()).digest()
        x = int.from_bytes(digest, "big") % p
        P = c.lift_x(x)
        if P is not None:
            return P
        ctr += 1


def generate_prime_order_curve(bits: int, rng: secrets.SystemRandom | None = None) -> Curve:
    rng = rng or secrets.SystemRandom()
    if bits < 16 or bits > 72:
        raise ValueError("bits must be in [16, 72] for BSGS point counting")
    while True:
        p = _random_prime(bits, rng)
        a = rng.randrange(p)
        b = rng.randrange(1, p)
        if (4 * a ** 3 + 27 * b ** 2) % p == 0:
            continue
        tmp = Curve(p, a, b, 0, 0, 1)
        P = None
        while P is None:
            P = tmp.lift_x(rng.randrange(p))
        N = point_order_in_hasse(p, a, b, P)
        if N is None or not is_probable_prime(N) or N == p:
            continue
        if N <= 4 * math.isqrt(p) + 4:
            continue
        # Reject curves with small embedding degree (MOV) — cheap for tiny k.
        if any(pow(p, k, N) == 1 for k in range(1, 21)):
            continue
        return Curve(p, a, b, P[0], P[1], N)


def make_challenge(curve: Curve, seed: str) -> dict:
    """Hash-derive G and Q on ``curve``; returns curve dict + Q."""
    G = hash_to_point(curve.p, curve.a, curve.b, curve.n, "G", seed)
    Q = hash_to_point(curve.p, curve.a, curve.b, curve.n, "Q", seed)
    c = Curve(curve.p, curve.a, curve.b, G[0], G[1], curve.n)
    assert c.in_subgroup(G) and c.in_subgroup(Q)
    return {"curve": c.to_dict(), "Q": [hex(Q[0]), hex(Q[1])], "seed": seed}
