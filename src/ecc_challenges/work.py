"""Curve operations estimated from distinguished-point counts, as powers of two.

Every rho step lands on a distinguished point with a fixed probability p, so D
stored points stand for about D/p steps. That is an estimate of work done, not
a measurement: it assumes the points came from honest walks, which the sampled
replays and per-point checks support but do not prove.

Rho on a group of effective size N finds its first collision after
E = sqrt(pi*N/2) steps on average, and has found one after k steps with
probability 1 - exp(-k**2/(2N)) = 1 - exp(-(pi/4)*(k/E)**2). Separate walks on
one curve never collide with each other, so their (k/E)**2 terms add.
"""

from __future__ import annotations

from math import comb, expm1, log2, pi

from .protocol import DP_WEIGHT

SIGMA_DEGREE = 131
# The ECC2K-130 sigma walk keeps x in the 131-bit normal basis, where every
# subgroup point's x has even Hamming weight, and stops at weight <= DP_WEIGHT.
SIGMA_DP_LOG2 = log2(sum(comb(SIGMA_DEGREE, w) for w in range(0, DP_WEIGHT + 1, 2))) - (SIGMA_DEGREE - 1)


def sigma_expected_log2(order: int) -> float:
    """The sigma walk steps between classes of 2*131 points (negation and Frobenius)."""
    return (log2(pi * order / (4 * SIGMA_DEGREE))) / 2


def estimate(walks: list[tuple[int, float, float]]) -> dict:
    """Combine (points, DP probability log2, expected steps log2) for one curve."""
    steps = sum(points * 2.0 ** -dp_log2 for points, dp_log2, _ in walks)
    share = sum((points * 2.0 ** -dp_log2 / 2.0 ** expected) ** 2 for points, dp_log2, expected in walks)
    return {
        "operations_log2": round(log2(steps), 2) if steps else None,
        # sqrt of the summed squares: the single-walk fraction with the same odds.
        "expected_work_percent": _figures(100 * share ** 0.5),
        "estimated_completion_probability_percent": _figures(-100 * expm1(-pi / 4 * share)),
    }


def _figures(value: float) -> float:
    """Four significant figures, so a tiny share stays distinguishable from none."""
    return float(f"{value:.4g}")
