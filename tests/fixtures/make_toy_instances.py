"""Generate tests/fixtures/toy_instances.json: small curves with a prime-order
subgroup of 2^24..2^36 elements, for planted-solve tests and the walk
measurements. Deterministic in SEED; regenerate with

    PYTHONPATH=src python tests/fixtures/make_toy_instances.py > tests/fixtures/toy_instances.json

The group order comes from baby-step giant-step in the Hasse interval (a
unique multiple of a point's order), and every instance is then checked by
n*G = O, n*Q = O and a Hasse-interval test. Not for production curves.
"""
import json
import random
import sys
from math import isqrt

from ecc_challenges.multicurve.curves import (Instance, binary_inverse, is_probable_prime,
                                              polynomial_is_irreducible)

SEED = 20261024


def random_prime_3mod4(rng, bits):
    while True:
        p = rng.getrandbits(bits) | (1 << (bits - 1)) | 3
        if p % 4 == 3 and is_probable_prime(p):
            return p


def raw(kind, modulus, bits, a, b):
    """An Instance used only for its affine arithmetic."""
    return Instance("toy", kind, bits, modulus, a, b, 2, 1, (0, 0), (0, 0))


def random_point(inst, rng):
    while True:
        x = rng.randrange(1, inst.modulus if inst.field_kind == "prime" else 1 << inst.bits)
        if inst.field_kind == "prime":
            p = inst.modulus
            rhs = (x * x * x + inst.a * x + inst.b) % p
            y = pow(rhs, (p + 1) // 4, p)
            if y * y % p == rhs:
                return (x, y)
        else:
            m = inst.bits
            xx = inst._fmul(x, x)
            c = inst._fmul(xx, x) ^ inst._fmul(inst.a, xx) ^ inst.b      # y^2 + xy = c
            # z = y/x solves z^2 + z = c / x^2 (odd m: half-trace when Tr = 0)
            t = inst._fmul(c, binary_inverse(inst._fmul(x, x), inst.modulus))
            z, s = 0, t
            for _ in range((m - 1) // 2 + 1):
                z ^= s
                s = inst._fmul(s, s)
                s = inst._fmul(s, s)
            # z is the half trace of t; check that z^2 + z = t
            if inst._fmul(z, z) ^ z == t:
                y = inst._fmul(z, x)
                pt = (x, y)
                if inst.on_curve(pt):
                    return pt


def group_order(inst, rng):
    q = inst.modulus if inst.field_kind == "prime" else 1 << inst.bits
    width = isqrt(4 * q) + 2
    low = q + 1 - width
    step = isqrt(2 * width) + 1
    for _ in range(20):
        P = random_point(inst, rng)
        baby, cur = {}, None
        for j in range(step):
            baby.setdefault(cur, j)
            cur = inst._add(cur, P)
        # cur == step * P
        giant = cur
        target = inst.neg(inst.mul(low, P)) if low > 0 else None
        hits, probe = [], target
        for i in range((2 * width) // step + 2):
            if probe in baby:
                k = i * step + baby[probe]
                if k <= 2 * width:
                    hits.append(low + k)
            probe = inst._add(probe, inst.neg(giant)) if giant is not None else probe
        if len(hits) == 1:
            return hits[0]
    raise RuntimeError("no unique order")


def with_prime_subgroup(inst, order, rng, max_cofactor):
    for h in range(1, max_cofactor + 1):
        if order % h == 0 and is_probable_prime(order // h):
            n = order // h
            while True:
                G = inst.mul(h, random_point(inst, rng))
                if G is not None:
                    return n, h, G


def trinomial_or_pentanomial(m):
    for k in range(1, m):
        if polynomial_is_irreducible((1 << m) | (1 << k) | 1):
            return (1 << m) | (1 << k) | 1
    for a in range(1, m):
        for b in range(1, a):
            for c in range(1, b):
                if polynomial_is_irreducible((1 << m) | (1 << a) | (1 << b) | (1 << c) | 1):
                    return (1 << m) | (1 << a) | (1 << b) | (1 << c) | 1
    raise RuntimeError


def make(kind, bits, rng, index):
    for _ in range(100000):
        if kind == "prime":
            p = random_prime_3mod4(rng, bits)
            a, b = rng.randrange(p), rng.randrange(p)
            if (4 * a**3 + 27 * b * b) % p == 0:
                continue
            modulus, field_bits, max_h = p, bits, 2
            inst = raw("prime", modulus, field_bits, a, b)
        else:
            modulus = trinomial_or_pentanomial(bits)
            a, b = rng.choice((0, 1)), rng.randrange(1, 1 << bits)
            inst = raw("binary", modulus, bits, a, b)
            max_h = 4
        order = group_order(inst, rng)
        found = with_prime_subgroup(inst, order, rng, max_h)
        if not found:
            continue
        n, h, G = found
        d = rng.randrange(2, n - 1)
        Q = inst.mul(d, G)
        toy = Instance(f"toy-{kind}-{n.bit_length()}-{index}", kind, field_bits if kind == "prime" else bits,
                       modulus, a, b, n, h, G, Q)
        toy.validate()
        return {"id": toy.id, "field_kind": kind, "bits": toy.bits, "modulus": hex(modulus),
                "a": hex(a), "b": hex(b), "n": hex(n), "h": h, "G": [hex(v) for v in G],
                "Q": [hex(v) for v in Q], "planted_d": hex(d), "group_order": order}
    raise RuntimeError("no curve found")


def main():
    rng = random.Random(SEED)
    out = []
    for kind, sizes in (("prime", (24, 28, 32, 36)), ("binary", (25, 29, 33))):
        for i, bits in enumerate(sizes):
            out.append(make(kind, bits if kind == "prime" else bits, rng, i))
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
