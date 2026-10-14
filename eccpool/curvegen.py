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


# ------------------------------------------------------------------ Goldilocks
GOLDILOCKS = 2 ** 64 - 2 ** 32 + 1


def _pollard_rho(n: int) -> int:
    if n % 2 == 0:
        return 2
    rng = secrets.SystemRandom()
    while True:
        y, c, m = rng.randrange(1, n), rng.randrange(1, n), 128
        g = r = q = 1
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                ys = y
                for _ in range(min(m, r - k)):
                    y = (y * y + c) % n
                    q = q * abs(x - y) % n
                g = math.gcd(q, n)
                k += m
            r *= 2
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = math.gcd(abs(x - ys), n)
        if g != n:
            return g


def factor(n: int) -> list[int]:
    """Prime factors of n with multiplicity (n up to ~2^80 is quick)."""
    out: list[int] = []
    for p in (2, 3, 5, 7, 11, 13):
        while n % p == 0:
            out.append(p)
            n //= p
    stack = [n] if n > 1 else []
    while stack:
        m = stack.pop()
        if is_probable_prime(m):
            out.append(m)
            continue
        d = _pollard_rho(m)
        stack += [d, m // d]
    return sorted(out)


def generate_goldilocks_curve(subgroup_bits: int,
                              rng: secrets.SystemRandom | None = None) -> tuple[Curve, int]:
    """A curve over F_q, q = 2^64 - 2^32 + 1 (the STARK field), whose group
    order has a prime factor n of exactly ``subgroup_bits`` bits.

    Returns (curve with G of order n, cofactor h = #E / n). Rho runs in the
    order-n subgroup, so the challenge costs ~sqrt(pi n / 2) steps however
    large the field is.
    """
    if not 24 <= subgroup_bits <= 64:
        raise ValueError("subgroup_bits must be in [24, 64]")
    rng = rng or secrets.SystemRandom()
    p = GOLDILOCKS
    while True:
        a = rng.randrange(p)
        b = rng.randrange(1, p)
        if (4 * a ** 3 + 27 * b ** 2) % p == 0:
            continue
        tmp = Curve(p, a, b, 0, 0, 1)
        P = None
        while P is None:
            P = tmp.lift_x(rng.randrange(p))
        N = point_order_in_hasse(p, a, b, P)
        if N is None:
            continue
        cands = [f for f in set(factor(N)) if f.bit_length() == subgroup_bits]
        if not cands:
            continue
        n = cands[0]
        if n == p or any(pow(p, k, n) == 1 for k in range(1, 21)):
            continue
        h = N // n
        G = tmp.mul_raw(h, P)
        if G is None:
            continue
        curve = Curve(p, a, b, G[0], G[1], n)
        if curve.mul_raw(n, G) is not None:
            continue
        return curve, h


def make_subgroup_challenge(curve: Curve, h: int, seed: str) -> dict:
    """Hash-derive G and Q in the order-n subgroup (clear the cofactor h)."""
    def derive(label: str) -> tuple[int, int]:
        ctr = 0
        while True:
            P = hash_to_point(curve.p, curve.a, curve.b, curve.n, f"{label}{ctr or ''}", seed)
            S = Curve(curve.p, curve.a, curve.b, 0, 0, curve.n).mul_raw(h, P)
            if S is not None:
                return S
            ctr += 1
    G, Q = derive("G"), derive("Q")
    c = Curve(curve.p, curve.a, curve.b, G[0], G[1], curve.n)
    assert c.in_subgroup(G) and c.in_subgroup(Q)
    return {"curve": c.to_dict(), "cofactor": hex(h), "Q": [hex(Q[0]), hex(Q[1])],
            "seed": seed, "field": "goldilocks"}
