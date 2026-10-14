# Proofs instead of audits: where a STARK helps

Status: **design note, not implemented**. Spot audits (`DESIGN.md`) are what
the pool trusts today.

## What a proof would buy

A STARK (or SNARK) proving "this DP is `f^L(start(key, nonce))` and the
walk hits no earlier DP" makes every share fully verified, not sampled:

- no audit randomness to trust, and verification anyone can repeat
  (third parties can check payouts);
- no need to hold credit while audits come in; payouts could be immediate;
- the share no longer needs checkpoints.

## Why it doesn't pay for ordinary shares

The prover must re-execute the walk inside the proof system. With current
provers that costs roughly 10^2–10^4× the native step, depending on how
the curve arithmetic maps onto the proof field. A pool where every share
costs 100× the work it represents is 99% overhead. Spot audits cost the
*host* about `audit_rate × k / L` of the work, which is well under 1%.

## The design that does fit: native-field challenges

The cost is dominated by **non-native arithmetic**: emulating F_p inside the
STARK field F_q. If the challenge curve is defined **over the STARK's own
field** (for example Goldilocks `q = 2^64 − 2^32 + 1`, or BabyBear/M31
extension fields), one affine step `P + R_j` is three native constraints:

```
λ·(x_R − x_P) = y_R − y_P
x' = λ² − x_P − x_R
y' = λ·(x_P − x') − y_P
```

What's left is selecting `R_j`, which needs the bits `(x >> dp_bits) mod r`,
and the DP test (`dp_bits` low bits zero). Both need a bit decomposition of
`x`, about 64 boolean columns per row over Goldilocks. That's around 70
columns per step, an AIR that a modern prover runs at millions of rows per
second. For curves over such fields, a proof per share costs perhaps
10–50× the native walk instead of 10^3+: still overhead, but it could be
worth it for a fully trustless public challenge.

## A cheaper middle ground: prove only what's challenged

Keep the checkpoint shares, but answer audits with proofs:

1. The worker submits a share as today.
2. The server samples a segment (Fiat–Shamir from the share hash, so the
   sample is publicly verifiable rather than server-chosen).
3. The worker returns a STARK for that one segment (k steps), and the server
   or anyone else verifies it in milliseconds.

The audit cost moves from the host to the worker, the sampling becomes
publicly checkable, and the prover overhead applies only to sampled
segments (`audit_rate × k / L` of the walk). This is the version worth
building when the pool is open to the public and payouts must be auditable
by outsiders.

## Hook in this codebase

`Pool.audit_share` is the single place a segment is judged. A proof-backed
audit would add a `proof` kind to the `audits` table and an endpoint
`POST /v1/audits/{id}/proof` where the worker uploads the segment proof. The
verifier (Winterfell, Plonky3 or Stwo via a small native binding) would
replace `replay_segment`.
