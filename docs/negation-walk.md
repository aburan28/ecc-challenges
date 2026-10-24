# Negation-class walk (opt-in, disabled by default)

Status: implemented for the CPU engine and the Python reference; the CUDA
kernel dispatches to the same shared code but **has not been compiled or run**
(this change was built on a host with no GPU and no `nvcc`); the Metal backend
refuses the walk. Nothing is enabled: the v2 adding walk stays the default for
every campaign, v2 namespaces, records and receipts are byte for byte
unchanged, and the production ECC2K-130 v1 Frobenius walk is not touched. The
measurement behind the claims below is in
[`verification/negation-walk-20261024`](../verification/negation-walk-20261024/README.md).

## Why

The v2 walk has no automorphism quotient, so a solve takes about
`sqrt(pi*n/2)` steps. Negation `P -> -P` is an automorphism of every curve
here, so a walk on the classes `{P, -P}` searches `n/2` objects and needs about
`sqrt(pi*n/4)` steps, a factor `1/sqrt(2)` (0.707). The price is per-step work
(a sign test and negation of the coefficients) and the **fruitless cycles** that
negation creates, which must be detected and escaped.

## The walk

**Classes.** The representative of `{P, -P}` is the point with the smaller `y`
as an unsigned integer: `min(y, p - y)` on prime fields, `min(y, x XOR y)` on
binary fields (`-(x, y) = (x, x + y)` there). A point with `y = -y` is its own
representative; infinity is its own class. States, DPs and uploaded records are
always the representative.

**Partition and DP predicate** are functions of the class. They hash the
**canonical encoding**: `SHA-256(x || y)` of the *representative*, fixed-width
big-endian as in v2. The first byte's low 5 bits select the jump and the
trailing `dp_bits` bits of the digest decide distinguishedness, exactly as in
v2 but applied to the representative. Hashing `x` alone would also be a
function of the class (`x(P) = x(-P)`), but hashing the representative's `x||y`
keeps the digest function, the compact point index fingerprint (first 8 bytes
of `SHA-256(x||y)` of the stored record) and the exact-point index key
identical to v2: a stored record is the representative, so two records of one
class are *equal points* and the existing conditional insert finds the
collision.

**Coefficients.** A step adds the jump `T[j] = a_j G` (the same 32 jumps as v2:
`b` unchanged) and, if the sum is not the representative, replaces it by its
negative and negates `(a, b)` modulo `n`. The invariant `R = aG + bQ` holds for
the stored representative at every step. As in v2 every jump has `db = 0`, so a
trail's `b` is its start's `b` up to sign; the verifier checks that a DP's `b`
is the start `b` derived from the run ID and trail ID **or its negative**
(`check_trail`), which keeps the DP tied to a server-assigned trail.

## Fruitless cycles

With the sign flipped and the same partition twice, `P -> Q = -(P + T_j) -> P`:
a 2-cycle, with probability about `1/(2r)` per step for `r = 32` jumps, about
1.6%. Without handling the walk is trapped (the measurement's ablation arm
needs 11 and 76 times the steps of the v2 walk at the two sizes it ran). The standard remedies are in Wiener and
Zuccherato (SAC 1998), Bos, Kleinjung and Lenstra, *On the use of the negation
map in the Pollard rho method* (ANTS-IX, LNCS 6197, 2010), and Bernstein,
Lange and Schwabe, *On the correct use of the negation map in the Pollard rho
method* (PKC 2011, LNCS 6571): reduce 2-cycles by looking ahead, check for
longer cycles periodically, and leave a detected cycle by a deterministic move
that depends only on the cycle. The parameters below are this implementation's
own choices; they are not quoted from those papers.

1. **Look-ahead 2-cycle reduction.** After computing the step with jump `j`,
   if the sign flipped and the new point's partition is again `j`, the step is
   redone with jump `j+1` (then `j+2`, `j+3`; at most 4 tries). The rule uses
   only the current class, so walks that meet still merge. It costs about one
   extra point addition per 62 steps. In the unit test it removes every
   `P_i = P_{i+2}` return that the plain walk shows in hundreds.
2. **Periodic cycle check.** Every 4 steps the class fingerprint (59 digest
   bits and the 5-bit partition) is compared with the last 32. A repeat at
   distance `L` is a cycle of length `L` (look-ahead leaves 4-cycles and
   longer ones, about once per few thousand steps).
3. **Escape.** The walk advances (at most `L-1` steps) to the cycle member `M`
   with the smallest fingerprint and leaves it by adding `c*G`, where
   `c = 1 + (fingerprint(M) >> 5) mod 65535` and `(a, b) -> (a + c, b)` (then the
   usual representative and sign rule). `c*G` is a small multiple of `G`; the
   table jumps are full-width random multiples, so no short path of table jumps sums
   to `-c*G` (that would need a small-coefficient relation among random scalars,
   probability about 2^-(field bits)) and the walk cannot fall back into the cycle, and `b` is unchanged,
   so the trail binding above survives. The move depends only on the cycle, so
   every walk that falls into it leaves at the same point. It costs about 24 point
   additions (double-and-add over 16 bits) per escape, one escape per few
   thousand steps. Doubling `M`, the move usually described, multiplies `(a, b)`
   by 2 and would break the `b` binding, so it is not used. Escapes that add a
   *table* jump were tried first and fail, each found by measurement (see the
   study's `PROTOCOL-AMENDMENT-1.md` and `-2.md`): a fixed offset alone lets a
   4-cycle alternating jumps `i` and `i + 16` re-enter itself; an escape step
   without the look-ahead undoes itself about once in 64 escapes; and even with
   the look-ahead and a jump no cycle step can have used, the walk rejoins the
   cycle through a three-step reflection (escape jump, a jump the cycle uses,
   escape jump again) about once in several hundred cycles. Because the escape is
   deterministic the walk then repeats it until the trail ends, at a distinguished
   point or at the trail cap (32 x 2^d steps, about 5 x 10^11 at the worker's
   density), so a captured trail produces no point. In the measurement such
   captures happen early in a trail (about 1 in 900 trails at 2^34.5 with the
   table-jump escape, none with the c*G escape in 22,224 trails). The
   measurement keeps both earlier rules (`job.reserved` 3 and 4, plus 2 for the
   ablation; none is ever accepted from a file) so each stays reproducible.

All of these moves are transitions and count as steps. The window lives in one
engine call (one round), not in the 216-byte state, so the walk is a function of
the start state **and the round size**; the cycle logic never overruns a round
(an escape is only attempted when it fits), so an active lane advances by
exactly the requested steps. The server does not replay walks; it verifies
each record algebraically (below). A replay, to be exact, must use the same
`ECC_STEPS_PER_ROUND`; that is not yet part of the namespace, so replay is a
reference-implementation and test facility, not a server check.

## Measured (CPU, planted toy curves)

From the study in
[`verification/negation-walk-20261024`](../verification/negation-walk-20261024/README.md): on prime
toy curves of order 2^23 to 2^34.5 (300 solves per arm and size, same planted instances) the shipped
walk needs 0.700 of the v2 walk's steps (95% CI 0.673 to 0.728, geometric mean over four sizes; the
quotient predicts 0.707), at about 6% more time per step on this CPU (0.744 of the v2 time per solve, derived); the
v2 walk needs 1.34 sqrt(n) steps and the negation walk 0.94 sqrt(n) at these sizes (theory 1.25 and 0.89).
The three escape rules the study measured are all in the source (`job.reserved` 3, 4 and 1); only the last
leaves no trapped trail in 1,800 solves. No GPU result: the CUDA kernel was not compiled or run.

## Namespace, records and verification

The walk is its own namespace: `walk.id = certicom-affine-negation-class32-v1`,
`native_job = ECCJOB04`, `automorphism_quotient = negation`, the cycle
parameters, and the class definition are all in the hashed manifest. The
record codec and record size are the v2 ones. DP bits follow
`floor(log2 sqrt(pi*n/4)) - 23`, capped at 34 (`dp_bits_for(n, walk)`).

The verifier checks, per record: widths and ranges, **the point is the class
representative** (a negative written as a valid `(a, b)` pair is rejected),
the DP predicate of the class, the curve equation, `R = aG + bQ`, and the
trail's `b` (start or negative). It does not check the path or `a`.

**Collisions.** Equal representatives with different `(a, b)` give
`d = (a1 - a2)/(b2 - b1)`. `Namespace.solve_class_collision` also handles
records on opposite members of a class: `R1 = -R2` gives
`d = -(a1 + a2)/(b1 + b2)`. The sign is read off the points and the candidate is
accepted only if `dG = Q`, so a wrong sign can never be returned as a
solution. The pipeline and the archive tool use one solver for both walks; a
verified negation-walk receipt also records the `sign`.

## Switching it on

Server: `MULTICURVE_NEGATION_ENABLED=true`, or key IDs in
`MULTICURVE_NEGATION_CANARY_KEYS` (both default off, and the v2 gate
`MULTICURVE_ENABLED` or its canary list must also admit the key). `GET
/v2/config?campaign=...&walk=<id>` and `POST /v2/register` with an optional
`"walk"` field select it; without it everything is v2. Worker: `ECC_WALK=negation`
(default `v2`); a state directory bound to one namespace is never reused for
another. Engines: `ECCJOB04` jobs need an engine built from this source;
older engines reject the magic. The status page groups work per namespace and
uses `sqrt(pi*n/4)` for negation-walk namespaces.

A negation namespace starts with zero points and its DPs never combine with v2
DPs, so enabling it for a campaign does not use work already done there.

## Not done: Frobenius classes on the Koblitz campaigns

`ecc2k-95`, `-108`, `-130`, `-163`, `-238` and `-358` have a Frobenius
`phi(x, y) = (x^2, y^2)` with `phi(P) = lambda P`, `lambda^2 - mu*lambda + 2 = 0 mod n`
(`mu = +-1`). Classes `{ +-phi^i(P) : 0 <= i < m }` have `2m` elements and give a
further `sqrt(m)`: `sqrt(pi*n/(4m))` steps in total (the v1 sigma walk is this
quotient, for ECC2K-130 only). Design for a follow-up namespace
`certicom-affine-frobenius-class32-v1`:

* Representative: the minimum, over the `2m` points `+-phi^i(P)`, of the
  big-endian `(x, y)` encoding. Cost per step: `m` field squarings (in the
  polynomial basis a squaring is a linear map; in a normal basis it is a
  rotation) plus `m` comparisons; this is the main per-step price and should be
  measured before enabling.
* Coefficients: `phi^i(P) = lambda^i P`, so a step that selects `+-phi^i`
  multiplies `(a, b)` by `+-lambda^i mod n`. Jump `db = 0` no longer keeps `b`
  fixed: `b` becomes `+-lambda^i b0`, so the verifier's `b` check must accept
  the set `{ +-lambda^i b0 }` (`2m` values).
* Partition and DP: hash the minimal encoding, as here.
* Cycles: orbits of length 1 or 2 under `phi` and short cycles from the `2m`
  quotient are likelier than with negation alone; the look-ahead has to cover
  the Frobenius jump sets, and the escape should again leave the span of the
  table jumps (add `c*G` or `c*phi^i(G)`, adjusting `(a, b)` by `c` and
  `lambda^i`), because the table-jump escapes failed here (Bos-Kleinjung-Lenstra
  and Bernstein-Lange-Schwabe give the cycle analysis; the ECC2K-130 effort
  of Bailey et al., *Breaking ECC2K-130*, used a Frobenius-reduced iteration).
* Shared core: add a new `reserved` value (5 or higher; 3 and 4 are taken by the measured negation variants) to `ec_core.h` with the same dispatch;
  the field arithmetic needed (squaring by repeated `binary_xtime`) exists.
