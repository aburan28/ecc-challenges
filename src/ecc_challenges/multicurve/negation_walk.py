"""Pure-Python reference of the negation-class walk (ECCJOB04, reserved = 1).

It mirrors `walk_lane_neg` in worker/generic/ec_core.h operation for
operation, so the native engines can be checked bit for bit and a distinguished
point can be replayed from its trail start. It is slow (affine big-integer
arithmetic) and exists for tests, replay and audit, not for production walking.

Walk (see docs/negation-walk.md for the design and citations):

* States are class representatives (the smaller y of P and -P). A step adds
  the jump T[j] selected by the sha256 digest of the representative, replaces
  the sum by its representative and negates (a, b) mod n when it had to.
* 2-cycle look-ahead: a step that flipped the sign and lands on a point whose
  partition is again j would be undone by the next step (P -> Q -> P). The step
  is then redone with jump j+1, j+2, ... (at most 4 tries in total).
* Periodic cycle check with escape: every 4 steps the new class fingerprint is
  compared with the last 32. On a repeat of length L the walk moves to the
  cycle member M with the smallest fingerprint and leaves it by adding c*G, with
  c = 1 + (fingerprint(M) >> 5) mod 65535, (a, b) -> (a + c, b). c*G is outside
  the span of the table jumps with small coefficients, so no later step can
  return into the cycle, and b is unchanged. The window restarts with every
  call (round).

`mode` selects the variant: 1 is the walk (job.reserved == 1); 2 the ablation
without look-ahead and cycle handling; 3 and 4 are earlier escape rules kept so
the measurements stay reproducible: 3 escapes by a table jump without the
2-cycle look-ahead (it undoes itself about once in 64 escapes and then repeats
forever), 4 by a table jump no cycle step can have used, with the look-ahead
(it still rejoins the cycle through three-step reflections). See
verification/negation-walk-20261024.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache

from .codec import NEGATION_PARAMETERS, Namespace, Record

UINT64_MAX = (1 << 64) - 1
ATTEMPTS = NEGATION_PARAMETERS["lookahead_attempts"]
WINDOW = NEGATION_PARAMETERS["cycle_window"]
PERIOD = NEGATION_PARAMETERS["cycle_check_period"]
ESCAPE_OFFSET = 16        # table-jump escape variants (modes 3 and 4) only


@dataclass
class Counters:
    point_adds: int = 0
    lookaheads: int = 0
    escapes: int = 0


def fingerprint(digest: bytes) -> int:
    """59 digest bits, then the partition index in the low five bits."""
    return (int.from_bytes(digest[1:9], "big") & ~31) | (digest[0] & 31)


def escape_jump(members, index: int) -> int:
    """The jump that leaves a cycle: the first one, scanning up from
    (index + 16) mod 32, that no step of the cycle can have used. `members` are
    the partition indices of the cycle's classes; each step used one of the
    ATTEMPTS jumps starting at its class's partition."""
    used = 0
    for member in members:
        for k in range(ATTEMPTS):
            used |= 1 << ((member + k) & 31)
    escape = (index + ESCAPE_OFFSET) & 31
    for k in range(32):
        candidate = (escape + k) & 31
        if not (used >> candidate) & 1:
            return candidate
    return escape


def _step(ns: Namespace, record: Record, index: int, attempts: int, counters: Counters):
    """One class transition from `record`; returns (record, digest or None)."""
    if record.steps == UINT64_MAX:
        raise OverflowError("step counter exhausted")
    table = ns.table()
    n = ns.instance.n
    digest = None
    for k in range(attempts):
        j = (index + k) & 31
        delta, da, db = table[j]
        counters.point_adds += 1
        if k:
            counters.lookaheads += 1
        point = ns.instance.add(record.point, delta)
        a, b = (record.a + da) % n, (record.b + db) % n
        flipped = False
        digest = None
        if point is not None:
            point, flipped = ns.canonical_point(point)
            if flipped:
                a, b = ns.negate_coefficients(a, b)
            digest = ns.selector(point)
            if flipped and (digest[0] & 31) == j and k + 1 < attempts:
                continue
        break
    return Record(record.trail_id, record.steps + 1, point, a, b), digest


@lru_cache(maxsize=65536)
def _multiple_of_g(ns: Namespace, c: int):
    return ns.instance.mul(c, ns.instance.G)


def escape_multiplier(smallest: int) -> int:
    """c in 1..65535 from the cycle's smallest window entry."""
    return 1 + (smallest >> 5) % 65535


def _escape_small(ns: Namespace, record: Record, smallest: int, counters: Counters):
    """The escape move: add c*G, (a, b) -> (a + c, b), keep the representative."""
    if record.steps == UINT64_MAX:
        raise OverflowError("step counter exhausted")
    n = ns.instance.n
    c = escape_multiplier(smallest)
    counters.point_adds += 1
    point = ns.instance.add(record.point, _multiple_of_g(ns, c))
    a, b = (record.a + c) % n, record.b
    digest = None
    if point is not None:
        point, flipped = ns.canonical_point(point)
        if flipped:
            a, b = ns.negate_coefficients(a, b)
        digest = ns.selector(point)
    return Record(record.trail_id, record.steps + 1, point, a, b), digest


def walk_lane(ns: Namespace, record: Record, active: int, steps: int,
              *, mode: int = 1, counters: Counters | None = None):
    """Advance one lane by `steps`; returns (record, active). `mode` 2 is the
    ablation (job.reserved == 2): no look-ahead, no cycle check; 3 is the walk
    as first measured (escape step without look-ahead)."""
    handling = mode != 2
    escape_attempts = 1 if mode == 3 else ATTEMPTS      # table-jump escapes only
    counters = counters if counters is not None else Counters()
    if not active:
        return record, active
    digest = None if record.point is None else ns.selector(record.point)
    index = 0 if digest is None else digest[0] & 31
    ring: list[int] = []
    pushed = 0
    done = 0

    def push(value):
        nonlocal pushed
        if len(ring) < WINDOW:
            ring.append(value)
        else:
            ring[pushed % WINDOW] = value
        pushed += 1

    while done < steps:
        record, digest = _step(ns, record, index, ATTEMPTS if handling else 1, counters)
        done += 1
        if record.point is None:
            index, pushed = 0, 0
            ring.clear()
            continue
        if ns.distinguished(record.point):
            return record, 0
        index = digest[0] & 31
        if not handling:
            continue
        fp = fingerprint(digest)
        if pushed and done % PERIOD == 0:
            span = min(pushed, WINDOW)
            length = next((d for d in range(1, span + 1)
                           if ring[(pushed - d) % WINDOW] == fp), 0)
            if length and steps - done >= length:
                counters.escapes += 1
                smallest = min([fp] + [ring[(pushed - d) % WINDOW] for d in range(1, length + 1)])
                current = fp
                hop = 0
                while current != smallest and hop + 1 < length:
                    record, digest = _step(ns, record, index, ATTEMPTS, counters)
                    done += 1
                    hop += 1
                    if record.point is None:
                        break
                    if ns.distinguished(record.point):
                        return record, 0
                    index = digest[0] & 31
                    current = fingerprint(digest)
                if record.point is not None:
                    if mode == 1:
                        record, digest = _escape_small(ns, record, smallest, counters)
                    else:
                        escape = escape_jump(
                            [ring[(pushed - d) % WINDOW] & 31 for d in range(1, length + 1)], index)
                        record, digest = _step(ns, record, escape, escape_attempts, counters)
                    done += 1
                ring.clear()
                pushed = 0
                if record.point is None:
                    index = 0
                    continue
                if ns.distinguished(record.point):
                    return record, 0
                index = digest[0] & 31
                push(fingerprint(digest))
                continue
        push(fp)
    return record, active


def reference_round(ns: Namespace, states, steps: int, *, mode: int = 1,
                    counters: Counters | None = None):
    """`states` are (Record, active) pairs; returns the same after one round."""
    out = []
    for record, active in states:
        out.append(walk_lane(ns, record, active, steps, mode=mode, counters=counters))
    return out


def replay_to_distinguished(ns: Namespace, start: Record, round_steps: int, limit: int):
    """Replay a trail from its start in rounds of `round_steps` steps (the
    window restarts every round, as in the engine). Returns the distinguished
    record, or None if `limit` steps pass first."""
    record, active = replace(start), 1
    while active and record.steps < limit:
        record, active = walk_lane(ns, record, active, min(round_steps, limit - record.steps))
    return None if active else record
