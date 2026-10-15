# STARK verification

Jobs choose how shares are checked (`--verification`):

| Mode | What the worker sends | What the server checks | Trust left |
|---|---|---|---|
| `audit` | share + checkpoints | replays a random segment of ~5% of shares | the server's audit coins; credit is provisional until paid with a lag |
| `stark` | share + **STARK proof of the whole walk** | verifies every proof (~1–4 ms) | none: every credited share is proven |
| `stark-challenge` | share + checkpoints; then a **proof of one segment** when challenged | random segment of a fraction of shares, proven by the worker | the server's challenge coins (same as `audit`), but each audit verdict is a proof anyone can re-check |

The prover is `stark/` (Rust, [Winterfell](https://github.com/facebook/winterfell)
0.13.1). The server calls it through `eccpool/stark.py` (a pool of
long-lived `eccstark` processes speaking line-delimited JSON). STARK modes
need a curve over the Goldilocks field; the published ones are
`challenges/gl32.json` … `gl64.json`.

## Why Goldilocks curves

A STARK proves a computation over its own field, here Goldilocks,
`q = 2^64 − 2^32 + 1`. If the challenge curve is defined over that same
field, one walk step `P + R_j` is three native constraints. A curve over any
other prime would need non-native arithmetic, roughly 50× more constraints.

Challenge difficulty comes from a **prime-order subgroup** instead of the
field size: `gl40` is a curve over the 64-bit Goldilocks field whose group
order has a 40-bit prime factor n. G and Q lie in the order-n subgroup and
are hash-derived from the public seed with the cofactor cleared, so rho costs
√(πn/2) steps and nobody knows log_G(Q).

## The statement proven

Public inputs: start `P_0`, end, step count `s`, `end_is_dp`, `dp_bits`, the
multipliers `R_j`. The proof says:

- `P_{i+1} = P_i + R_{idx(P_i)}` for `i < s`, with `idx(P) = (x >> dp_bits) mod r`
- `P_s = end`
- no `P_i` with `1 ≤ i < s` is distinguished (the walk stopped at its first DP)
- if `end_is_dp`, `P_s` is distinguished

"Distinguished" and `idx` are read from the canonical integer `x < q`.

The server supplies `start = H(salt, key_id, nonce)·(G, Q)` itself. A proof
therefore covers this key's walk only: replaying it under another key fails
(`tests/test_stark.py::test_stark_mode_accepts_only_proven_shares`).

### AIR (`stark/src/lib.rs`)

73 columns, one row per walk point: x, y, the chord slope λ, the selected
multiplier `R`, `1/(R.x − x)`, the low `dp_bits` of x, its inverse, a
canonicity witness, and the 64 bits of x. 75 transition constraints, max
degree `log2 r` (5 for r = 32):

| Constraints | Purpose | Degree |
|---|---|---|
| `b(b−1)` ×64, `x = Σ b_i 2^i` | bit decomposition | 2, 1 |
| `z(1 − zw)`, `(1 − zw)·lo32` with `z = 32 − Σ hi bits` | the integer is `< q`, so the bits are canonical | 3, 3 |
| `R = multilinear-select(bits d … d+log r−1)` | `R` is exactly `R_idx` | log r |
| `inv·(R.x − x) = 1` | chord formula applies (no doubling or infinity) | 2 |
| `λ(R.x − x) = R.y − y`, `x' = λ² − x − R.x`, `y' = λ(x − x') − y` | `P' = P + R` | 2 |
| `slow = Σ_{i<d} b_i 2^i`, `mask·(slow'·inv' − 1)` | interior points are not distinguished | 1, 2 |

Boundary assertions: `P_0 = start`, `P_s = end`, and `slow_s = 0` if `end_is_dp`.
The trace length is fixed by `s` (`next_pow2(s + 2)`, at least 8), and the
verifier rejects any other length.

The curve equation is not in the circuit. The server checks that
start, multipliers, checkpoints and the DP are on the curve, and the chord
of two curve points stays on the curve.

### Proof parameters

27 FRI queries, blowup 8, 16 grinding bits, quadratic extension of
Goldilocks, Blake3: **96 bits of conjectured security** (Winterfell's
estimate). The verifier accepts **exactly this parameter set** and nothing
else.

That is conjectured security. *Proven* (unique-decoding) security at these
parameters is only about 40 bits. Raising the query count closes the gap at
the cost of proof size (~1.5 KB per extra query). Two things make the low
proven figure tolerable for a pool:

- A forgery has to beat FRI soundness, which no known attack comes close to.
- Even a forged share is worth little. Its DP and representation still pass
  the server's direct check, so it can never fake a solution. At worst it
  is a valid DP that was paid for without its walk.

Change `proof_options()` in `stark/src/lib.rs` to tune the parameters. The
prover and verifier must change together.

STARK jobs require `dp_bits ≤ 16`, and their walks are capped at 2^20 steps.
Longer walks would need trace lengths in the millions of rows.

### Independent review

A separate review pass went through the AIR, the verifier and the server
integration, looking for a way to get a false statement accepted. It found
none. It checked canonicity, multiplier selection, the chord gadget, mask
off-by-ones, public-input binding, the trace-length pin, degree
declarations and the challenge anchors. It did find a **verifier DoS**: a
proof claiming blowup 2 passed the conjectured-security check, then
panicked Winterfell while it built the AIR, which killed the `eccstark`
process. This is fixed by pinning the exact option set and catching panics
in the verifier and the CLI. The regression test is
`foreign_proof_options_are_rejected_without_panicking`.

### Soundness tests (`stark/tests/air.rs`, run with `--release`)

A malicious prover that edits the trace and proves anyway is rejected for
every one of these forgeries, as is a proof with foreign proof options: a wrong multiplier on one step, the
linear-sweep cheater (always adding the same point), the non-canonical
`x + q` bit encoding (which changes the selected multiplier), walking past
the first DP to claim a longer walk, claiming a non-DP end is distinguished,
and wrong start, end, step count or multipliers.

## Cost (measured, 4-core container)

`cargo run --release --example bench`:

| Steps proven | Prove | Verify | Proof | Prover cost / native step |
|---|---|---|---|---|
| 254 | 14 ms | 0.4 ms | 36 KB | 187× |
| 1,022 | 41 ms | 0.6 ms | 44 KB | 138× |
| 4,094 | 127 ms | 1.3 ms | 57 KB | 106× |
| 16,382 | 466 ms | 1.7 ms | 65 KB | 97× |
| 65,534 | 2.2 s | 3.9 ms | 79 KB | 115× |

The native walk measured 293 ns/step without batched inversion. A batched
or GPU walker is 10× or more faster, which makes the relative cost of
proving correspondingly worse.

End to end on `gl40` (two workers, 2 processes each, live HTTP server):

| Mode | Time to solve | Steps walked | Throughput | Shares | Proofs | Stored proofs |
|---|---|---|---|---|---|---|
| `stark` | 49.6 s | 1.29 M | 26 k steps/s | 1,314 | 1,314 (every share), all valid | 77 MB |
| `stark-challenge`, rate 0.25 | 4.7 s | 0.50 M | 107 k steps/s | 490 | 126 segment proofs, all passed | none |

Rho's running time varies a lot from run to run, so compare throughput,
not time to solve. In `stark-challenge` the bottleneck is not proving: it
is the server's pure-Python per-share checks (≈100 shares/s). In `stark` it
is the prover.

**Choosing a mode:** `stark` costs ~100× in prover time and gives a pool
that needs no trust in the operator. Every credited share is proven, and
proofs can be stored and published (`ECCPOOL_STORE_PROOFS=1`, the default).
`stark-challenge` costs `rate × (k/L) × ~100`, a few percent at rate 0.25
with the default `k = 2^d/8`. It keeps the incentive argument of audits
and makes each verdict a checkable proof.

## Challenge protocol (`stark-challenge`)

1. A share is committed. With probability `challenge_rate` (or the
   probation rate for a new key), the response carries
   `challenge: {challenge_id, segment, from_step, to_step, deadline}`, and
   the share's credit is held as `pending_proof`.
2. The worker proves that segment: from the anchor (start or checkpoint) to
   the next anchor, `to_step − from_step` steps, with the DP flag set on the
   last segment. It sends `POST /v1/challenges/{id}/proof`. The reference
   worker does this automatically and statelessly: it replays the walk from
   `(key, nonce)`, so it can also answer challenges left from before a
   restart (`GET /v1/challenges`).
3. A valid proof marks the share `verified`. An invalid proof, or no proof
   by the deadline (`ECCPOOL_PROOF_DEADLINE`, default 1 h), counts as a
   failed audit: the key is revoked and all its credit forfeited.

A worker can't pick which segment is challenged: the server draws it after
the share is committed. Fiat–Shamir selection, where the share's own hash
picks the segment, does **not** work here. A cheater who faked most
segments could re-randomise the fake checkpoints at no cost until the hash
lands on the one honest segment.

## Limits

- STARK modes need Goldilocks curves (any subgroup size up to 64 bits). A
  larger target, e.g. a 128-bit subgroup over an extension field, needs
  extension-field arithmetic in the AIR.
- No negation map (√2): it needs a fruitless-cycle rule inside the AIR.
- The native walker and the AIR abandon a walk if a step hits `x = R.x`
  (doubling). The Python reference would double instead. Either event has
  probability ~r/n per step, so for 32-bit and larger subgroups it never
  happens in practice.
- Proofs are not aggregated. Recursion, i.e. folding many share proofs
  into one, would cut verification and storage further, but Winterfell
  doesn't support it.
