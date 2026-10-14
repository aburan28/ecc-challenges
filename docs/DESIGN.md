# Design: verifiable shares for distributed Pollard rho

## The problem

In a distributed Pollard rho (van Oorschot–Wiener), workers walk a
pseudorandom function `f` on the group and report only *distinguished
points* (DPs). When two walks land on the same DP with different
representations `a·G + b·Q`, the discrete log falls out. The host pays
workers per DP, so it needs to know each DP is real work, not junk.

That breaks into two separate questions, with different costs to answer:

| Question | Cost to check | How |
|---|---|---|
| **Valid**: is the DP a real group element with a known representation, so it can never fake a solution? | ~1.5·log₂ n group ops (free next to the 2^d steps behind a DP) | every share, synchronously |
| **Useful**: was the DP reached by walking `f` from a start the worker could not choose, so that it represents ~2^d points of coverage? | k steps per audit (k = checkpoint interval) | random audits after the share is committed |

### Why validity alone is not enough

A cheater can make DPs that pass every cheap check without walking `f`:
step `R, R+G, R+2G, …` from a known `R = a·G + b·Q` until the x-coordinate
is distinguished. Each candidate costs one addition (less with `R±G` sharing
an inversion, or the GLV endomorphism on curves that have one), so the
cheater earns shares at the same or lower cost. But those DPs sit on no
`f`-trail: another walk can only collide with the single point itself, so
each one is worth about 1 point of coverage instead of 2^d.
`tests/test_pool.py::linear_sweep_shares` implements exactly this cheater.

## The share

A job publishes: curve `(p, a, b, G, n)`, target `Q`, a random `salt`, `dp_bits`,
`r` (number of multipliers), checkpoint interval `k` and `max_walk_len`.
Everything else is derived (`eccpool/walk.py`, `SPEC_VERSION = eccpool-walk-v1`):

- `R_j = c_j·G + d_j·Q`, `(c_j, d_j) = H("mult", salt, j)` for `j < r`
- `f(P) = P + R_{(x >> dp_bits) mod r}`
- `P` is distinguished when the low `dp_bits` bits of `x` are zero
- **start bound to the key**: `P_0 = a_0·G + b_0·Q`, `(a_0, b_0) = H("start", salt, key_id, nonce)`
- the walk stops at the first `i ≥ 1` with `P_i` distinguished; `L = i`

A share is `{nonce, length L, dp, a, b, checkpoints}`, with checkpoints
`P_k, P_2k, …` (every multiple of `k` below `L`). `tests/vectors.json` gives
known-answer vectors that a C or CUDA worker must reproduce byte for byte.

## Server checks

Every share, synchronously (`Pool._check`):

1. Well-formed: hex below the modulus, `1 ≤ L ≤ max_walk_len`, exactly `(L-1)//k` checkpoints.
2. DP and checkpoints are on the curve; DP is distinguished; no checkpoint is
   (the walk must stop at its first DP).
3. `dp == a·G + b·Q`.
4. Not a replay: `(job, key, nonce)` is unique, and the same DP with the same
   `(a, b)` from another key is rejected as `copied_dp`. A thief can't use a
   stolen DP under their own key, because the start depends on the key.

Then, after the share is committed (so the worker can't adapt to it):

5. **Segment audit**: with probability `audit_rate` (or `probation_audit_rate`
   for a key's first `probation_shares` shares), replay one uniformly random
   segment between consecutive anchors `start, P_k, P_2k, …, dp` and check it
   lands on the next anchor without passing through a DP. Cost: ≤ k steps.
6. **Full audit**: with probability `full_audit_rate`, replay the whole walk
   and also check that `(a, b)` is what the walk accumulates.

A share in which a fraction φ of segments are fabricated fails a segment
audit with probability φ. The linear-sweep cheater fabricates every segment,
so φ = 1. Skipping work always breaks segments: the only way to save steps
is not to take them.

## Sanctions

- A failed cheap check is a **strike**. `max_strikes` strikes **suspend** the
  key. An admin can reinstate it, which resets the strike count; honest
  clients with a bug get a second chance.
- A failed audit **revokes** the key and **forfeits all of its credit**, past
  and future. With probation audit rate 50% and steady-state 5%, a cheater
  is expected to be caught within ~2 shares during probation and ~20 after,
  and loses everything they earned. Cheating has negative expected value at
  any audit rate above zero, provided payouts lag accrual (see OPERATIONS.md).
- A revoked key can never be reinstated; issue a new key.
- DPs from revoked keys **stay in the collision table**. They passed the
  representation check, so they can only help: a collision against one is
  still a correct solve, which the server verifies (`k·G == Q`) before
  marking the job solved.

## Rate limiting and abuse

Token buckets (`eccpool/ratelimit.py`), all returning `429` with `Retry-After`:

| Bucket | Default | Purpose |
|---|---|---|
| per IP, all requests | 600/min | blunt flood control |
| per IP, failed auth | 10/min, burst 20 | brute force; hammering with a revoked key |
| per key, requests | 120/min (per key) | API politeness |
| per key, shares | 600/min, burst 200 (per key) | cost cap on verification work |

Also: request bodies over 2 MiB return `413`, batches hold at most 64 shares,
and a batch stops processing once its key is suspended mid-batch.

## What the host cannot be fooled about

- **A false solution**: impossible. Every stored DP has a verified representation,
  and the solution is checked before the job is marked solved.
- **Unearned credit, long term**: cheating is caught with the probability set
  by the audit rates and costs the cheater all their credit.
- **Withholding the winning DP** (Bitcoin pools' block-withholding attack):
  pointless here. A worker can't tell which DP will collide, because only
  the server holds the table, so withholding is blind and just loses credit.

## Known limits

- No negation map / equivalence classes yet (a √2 speedup). Adding one needs
  a deterministic fruitless-cycle escape in the spec, so that audits stay
  reproducible, and DP-ness defined on the class representative, so the
  endomorphism trick earns nothing.
- Python verification is ~1 ms per share for 64-bit groups. At thousands of
  shares per second, move `_check` to a native extension or a worker pool.
- One process, one SQLite database. The rate limiter is in-process; for more
  than one API process, move the buckets to Redis and the tables to Postgres
  (the SQL is portable).
- The audit coins come from `secrets`; they are not publicly verifiable. See
  `STARK.md` for proofs instead of audits.
