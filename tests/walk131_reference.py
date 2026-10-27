"""Python reference for walks 3 and 4, mirroring worker/generic/fast131.h.

The engine and this module must agree bit for bit on every round, including
the cycle escape, whose state resets at each engine call (a lane is loaded
fresh), so a chunked run and a single run of the same steps differ only if a
cycle straddles a boundary.
"""
from __future__ import annotations

from ecc_challenges.multicurve.codec import (
    ANCHOR_LOG2, M64, WALK_V4, Record, partition_x,
)

NORMAL, FINDMIN, SEEK = 0, 1, 2
ESCAPE = 32


def reference_round(ns, states, steps):
    """states: iterable of runtime.NativeState; returns the same type."""
    return [_lane(ns, s, steps) for s in states]


def _lane(ns, state, steps):
    inst, n = ns.instance, ns.instance.n
    table = list(ns.table())
    if ns.walk_version == WALK_V4:
        table.append(ns.escape_jump())
    v4 = ns.walk_version == WALK_V4
    point, a, b = state.point, state.a, state.b
    count_steps, active = state.steps, state.active
    negated = 0
    mode, count, length = NORMAL, 0, 0
    fcur = fprev = anchor = target = 0
    fcur_ok = fprev_ok = anchor_ok = 0
    anchor_lo = 0
    index = 0
    if active and point is not None:
        index = partition_x(point[0])
        fprev, fprev_ok = fcur, fcur_ok
        fcur, fcur_ok = point[0] & M64, 1
    done = 0
    while active and done < steps:
        done += 1
        if point is None:
            j = 0
        else:
            j = index
            if v4 and mode == SEEK:
                if fcur == target:
                    mode, j = NORMAL, ESCAPE
                else:
                    count -= 1
                    if count == 0:
                        mode = NORMAL
        delta, da, db = table[j]
        point = inst.add(point, delta)
        a = (a + da) % n
        count_steps += 1
        if point is None:
            index, mode = 0, NORMAL
            fcur_ok = fprev_ok = anchor_ok = 0
            continue
        if v4 and not ns.canonical_sign(point):
            point = inst.neg(point)
            a = (-a) % n
            negated ^= 1
        two_back, two_back_ok = fprev, fprev_ok
        index = partition_x(point[0])
        fprev, fprev_ok = fcur, fcur_ok
        fcur, fcur_ok = point[0] & M64, 1
        if ns.distinguished(point):
            active = 0
        if v4 and active:
            fp = fcur
            if mode == NORMAL:
                if two_back_ok and fp == two_back:
                    mode, target, count = SEEK, min(fprev, fp), 2
                elif anchor_ok and fp == anchor:
                    mode = FINDMIN
                    length = (count_steps - anchor_lo) & 0xFFFFFFFF
                    count, target = length, fp
            elif mode == FINDMIN:
                target = min(target, fp)
                count -= 1
                if count == 0:
                    mode, count = SEEK, length
            if count_steps & ((1 << ANCHOR_LOG2) - 1) == 0:
                anchor, anchor_lo, anchor_ok = fp, count_steps & 0xFFFFFFFF, 1
    if negated and b:
        b = (n - b) % n
    return type(state)(Record(state.trail_id, count_steps, point, a, b), active)
