"""The walk specification shared by the server and every worker.

A job fixes a curve, a target Q = k*G with k unknown, a salt and the
distinguished-point (DP) parameters. From those, everything is
deterministic:

* ``r`` multipliers ``R_j = c_j*G + d_j*Q`` with ``(c_j, d_j) = H("mult", salt, j)``.
* The iteration ``f(P) = P + R_{idx(P)}`` with
  ``idx(P) = (x >> dp_bits) mod r`` (the bits just above the DP bits).
* A point is distinguished when the low ``dp_bits`` of x are zero.
* A walk for API key ``key_id`` and worker-chosen ``nonce`` starts at
  ``P_0 = a_0*G + b_0*Q`` with ``(a_0, b_0) = H("start", salt, key_id, nonce)``.
  Binding the start to the key makes every walk attributable and makes
  another key's DP worthless to a thief.
* The walk stops at the first ``i >= 1`` with ``P_i`` distinguished; ``L = i``.
  Walks longer than ``max_walk_len`` are abandoned (they are cycling).
* The share carries checkpoints ``P_k, P_2k, ...`` for every multiple of the
  checkpoint interval ``k`` strictly below ``L``. A segment is the run of
  steps between two consecutive anchors (start, checkpoints, DP); any one
  segment can be replayed in ``k`` steps, which is what audits do.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .ec import Curve, Point

SPEC_VERSION = "eccpool-walk-v1"


def _h(*parts: bytes | str | int) -> int:
    h = hashlib.sha256()
    h.update(SPEC_VERSION.encode())
    for part in parts:
        if isinstance(part, int):
            part = part.to_bytes((part.bit_length() + 8) // 8, "big")
        elif isinstance(part, str):
            part = part.encode()
        h.update(len(part).to_bytes(4, "big"))
        h.update(part)
    return int.from_bytes(h.digest() + hashlib.sha256(h.digest()).digest(), "big")


@dataclass
class WalkSpec:
    curve: Curve
    Q: tuple[int, int]
    salt: str
    dp_bits: int
    r: int = 32
    checkpoint_interval: int = 0
    max_walk_len: int = 0
    _mults: list[tuple[int, int, tuple[int, int]]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.r & (self.r - 1):
            raise ValueError("r must be a power of two")
        if self.checkpoint_interval <= 0:
            self.checkpoint_interval = max(1, (1 << self.dp_bits) // 8)
        if self.max_walk_len <= 0:
            self.max_walk_len = 20 << self.dp_bits
        n = self.curve.n
        for j in range(self.r):
            c = _h("mult-c", self.salt, j) % n
            d = _h("mult-d", self.salt, j) % n
            R = self.curve.mul2(c, d, self.Q)
            if R is None:
                raise ValueError("degenerate multiplier; change the salt")
            self._mults.append((c, d, R))
        self._dp_mask = (1 << self.dp_bits) - 1

    @property
    def multipliers(self) -> list[tuple[int, int, tuple[int, int]]]:
        return self._mults

    def is_dp(self, P: Point) -> bool:
        return P is not None and (P[0] & self._dp_mask) == 0

    def index(self, P: tuple[int, int]) -> int:
        return (P[0] >> self.dp_bits) & (self.r - 1)

    def step(self, P: tuple[int, int]) -> Point:
        return self.curve.add(P, self._mults[self.index(P)][2])

    def start(self, key_id: str, nonce: str) -> tuple[int, int, Point]:
        n = self.curve.n
        a0 = _h("start-a", self.salt, key_id, nonce) % n
        b0 = _h("start-b", self.salt, key_id, nonce) % n
        return a0, b0, self.curve.mul2(a0, b0, self.Q)

    def n_checkpoints(self, length: int) -> int:
        return (length - 1) // self.checkpoint_interval

    def segment_bounds(self, length: int, seg: int) -> tuple[int, int]:
        k = self.checkpoint_interval
        return seg * k, min((seg + 1) * k, length)

    def n_segments(self, length: int) -> int:
        return self.n_checkpoints(length) + 1


@dataclass
class WalkResult:
    nonce: str
    length: int
    dp: tuple[int, int]
    a: int
    b: int
    checkpoints: list[tuple[int, int]]


def run_walk(spec: WalkSpec, key_id: str, nonce: str) -> WalkResult | None:
    """Honest walk from the key-bound start to the first DP (reference worker)."""
    n = spec.curve.n
    a, b, P = spec.start(key_id, nonce)
    if P is None:
        return None
    k = spec.checkpoint_interval
    mults = spec.multipliers
    add = spec.curve.add
    mask = spec._dp_mask
    shift = spec.dp_bits
    rmask = spec.r - 1
    checkpoints: list[tuple[int, int]] = []
    for i in range(1, spec.max_walk_len + 1):
        c, d, R = mults[(P[0] >> shift) & rmask]
        P = add(P, R)
        if P is None:
            return None
        a += c
        b += d
        if (P[0] & mask) == 0:
            return WalkResult(nonce, i, P, a % n, b % n, checkpoints)
        if i % k == 0:
            checkpoints.append(P)
    return None


def replay_segment(spec: WalkSpec, P: tuple[int, int], steps: int,
                   expect_dp_at_end: bool) -> tuple[Point, str | None]:
    """Walk ``steps`` steps from P. Returns (end point, error or None)."""
    for i in range(1, steps + 1):
        P = spec.step(P)
        if P is None:
            return None, "walk hit the point at infinity"
        if spec.is_dp(P) and i < steps:
            return P, f"distinguished point reached early (step {i} of {steps})"
    if expect_dp_at_end and not spec.is_dp(P):
        return P, "segment end is not distinguished"
    return P, None
