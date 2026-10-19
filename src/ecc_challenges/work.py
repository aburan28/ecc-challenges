"""Curve operations estimated from distinguished-point counts.

Every rho step lands on a distinguished point with a fixed probability p, so D
stored points stand for about D/p steps. That is an estimate of work done, not
a measurement: it assumes the points came from honest walks, which the sampled
replays and per-point checks support but do not prove.

Rho on a group of effective size N finds its first collision after
E = sqrt(pi*N/2) steps on average, and has found one after k steps with
probability 1 - exp(-k**2/(2N)) = 1 - exp(-(pi/4)*(k/E)**2). Separate walks on
one curve never report each other's collisions, so their (k/E)**2 terms add.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb, expm1, log2, pi, sqrt

from .protocol import DP_WEIGHT

SIGMA_DEGREE = 131
# The ECC2K-130 sigma walk keeps x in the 131-bit normal basis, where every
# subgroup point's x has even Hamming weight, and stops at weight <= DP_WEIGHT.
# "DP32" there is a weight bound, not 32 zero bits: one step in 2^28.41.
SIGMA_DP_LOG2 = log2(sum(comb(SIGMA_DEGREE, w) for w in range(0, DP_WEIGHT + 1, 2))) - (SIGMA_DEGREE - 1)


def sigma_expected_log2(order: int) -> float:
    """The sigma walk steps between classes of 2*131 points (negation and Frobenius)."""
    return log2(pi * order / (4 * SIGMA_DEGREE)) / 2


@dataclass(frozen=True)
class Walk:
    """The points one walk has stored on a curve, and what each one stands for."""
    dp_bits: int            # the DP setting as the project names it: DP32, DP34, ...
    points: int
    recent: int             # points stored within the rate window
    dp_log2: float          # log2 of the chance that a step is distinguished
    expected_log2: float    # log2 of the walk's expected steps to a collision


def progress(walks: list[Walk], expected_log2: float, window_hours: int) -> dict:
    """Work, odds and pace for one curve, against the walk it runs by default."""
    steps = [w.points * 2.0 ** -w.dp_log2 for w in walks]
    # Work and pace as shares of each walk's own expectation.
    shares = [s / 2.0 ** w.expected_log2 for s, w in zip(steps, walks)]
    paces = [w.recent * 2.0 ** (-w.dp_log2 - w.expected_log2) / window_hours for w in walks]
    work, share = sum(steps), sum(x * x for x in shares)
    per_hour = sum(w.recent * 2.0 ** -w.dp_log2 for w in walks) / window_hours
    hours = None
    if per_hour:
        # Hours t until sum(((k_i + r_i t)/E_i)^2) reaches 1 at the current rates.
        a = sum(v * v for v in paces)
        b = 2 * sum(x * v for x, v in zip(shares, paces))
        hours = 0.0 if share >= 1 else (-b + sqrt(b * b + 4 * a * (1 - share))) / (2 * a)
    return {
        "dp_bits": sorted({w.dp_bits for w in walks}) or None,
        "estimated_work_operations": work,
        "estimated_work_log2": round(log2(work), 2) if work else None,
        "expected_operations_log2": round(expected_log2, 2),
        # sqrt of the summed squares: the single-walk share with the same odds.
        "expected_work_percent": 100 * sqrt(share),
        "estimated_completion_probability_percent": -100 * expm1(-pi / 4 * share),
        "points_per_hour": round(sum(w.recent for w in walks) / window_hours, 1),
        "operations_per_second_log2": round(log2(per_hour / 3600), 2) if per_hour else None,
        "hours_to_expected_work": hours,
    }
