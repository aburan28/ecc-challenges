# Negation-class walk vs the v2 adding walk: steps per solve and rate

Study of `certicom-affine-negation-class32-v1` (the walk on the classes {P, -P}; design in
[`docs/negation-walk.md`](../../docs/negation-walk.md)) against the current v2 walk
(`certicom-affine-radding32-v2`) on the same planted toy curves. All runs on 2026-10-24 (frozen run
05:07 to 05:44 UTC in two sittings, amendment 1 06:42 to 06:49, amendment 2 solves 07:09 to 07:15, rate 08:20
to 08:24, long-trail check about 09:00 to 09:10), on a shared L0 cloud VM (4 vCPU Xeon @ 2.80 GHz, no hardware PMU, no GPU), g++ 13.3 -O2, single thread per
driver. Report PDF: [`report.pdf`](report.pdf); graph: [`steps-per-solve.svg`](steps-per-solve.svg)
(drawn by [`figure.py`](figure.py)).

**Nothing here is a GPU result.** There is no GPU and no `nvcc` on the build host: the CUDA kernel
dispatches to the same shared code (`walk_lane_any`) but was not compiled or run, and the Metal
backend refuses the walk. All numbers are CPU, from the shared header `worker/generic/ec_core.h`.

## Result

* **Steps per solve.** The shipped walk (arm B3) needs **0.700 (95% CI 0.673 to 0.728)** of the v2 walk's steps,
  geometric mean over the four primary prime sizes (n = 2^23.0, 2^27.6, 2^30.9, 2^34.5, 300 solves
  per arm and size, same planted d and start coefficients in both arms). The quotient predicts
  1/sqrt(2) = 0.707. Per size: 2^23.0 0.681 [0.629, 0.740], 2^27.6 0.677 [0.623, 0.734],
  2^30.9 0.725 [0.671, 0.780], 2^34.5 0.718 [0.665, 0.776]. Binary-field toys (n = 2^24, 2^27, 2^32,
  200 solves): see the table below. Calibration: the v2 walk takes 1.341 sqrt(n) steps (theory
  sqrt(pi/2) = 1.253), B3 takes 0.939 sqrt(n) (theory sqrt(pi/4) = 0.886).
* **Rate loss per step.** The frozen measurement (M2: single process, 15 alternating runs per
  arm, median ns per step) is noisy on this shared VM (IQR 5 to 10%) and its intervals include zero:
  - ecc2-79: 0.9% (95% CI -4.6 to 5.3), time per step B3/A 1.009, native overhead -0.007 GAE per step
  - eccp-79: 2.4% (95% CI -2.3 to 9.8), time per step B3/A 1.025, native overhead 0.008 GAE per step
  - toy-binary-33-2: 0.8% (95% CI -4.7 to 12.2), time per step B3/A 1.008, native overhead -0.007 GAE per step
  - toy-prime-24-0: 8.8% (95% CI -4.8 to 11.6), time per step B3/A 1.096, native overhead 0.090 GAE per step
  - toy-prime-35-3: -0.1% (95% CI -5.0 to 9.1), time per step B3/A 0.999, native overhead -0.017 GAE per step

  The tighter evidence is post hoc and supplementary: the walk time per step in the solve phase
  (10^6 to 5x10^7 steps per arm and instance, arms interleaved, A2/A within 1% on the prime instances) gives
  B3/A = 1.062 (mean of seven instances, range 1.034 to 1.079), i.e. a **rate loss of about
  6% per step**. It is what the operation counts predict: the look-ahead retries add 1.6% point
  additions, the c*G escapes 0.6% (adds per step 1.021 to 1.024 for B3), plus the sign test, the negation and
  the cycle window; the expensive part of a step on this CPU is the field inversion.
* **Net time per solve** (steps ratio x the frozen-M2 time-per-step ratio, per curve where both were
  measured; the intervals are wide because M2 is noisy):
  - toy-binary-33-2: 0.686 (95% CI 0.590 to 0.852)
  - toy-prime-24-0: 0.747 (95% CI 0.600 to 0.837)
  - toy-prime-35-3: 0.718 (95% CI 0.633 to 0.854)
  With the pooled steps ratio 0.700 and the supplementary time per step 1.062 the net time
  ratio is **0.744** (a derived figure, not a direct timing of whole solves).
* **Priced comparison** (matched-rho rule: group additions plus lambda x native operations, lambda =
  0, 0.1, 1 GAE per operation; the walk has 2a + 1 native operations per step against 1 for A): B3 beats A
  at lambda = 0 and 0.1 and loses at lambda = 1; the break-even lambda is 0.22 to 0.38 (table below). The
  order is therefore **not settled across the whole band**: it flips between 0.1 and 1 GAE per native
  operation. The measured native overhead (about 0.04 to 0.05 GAE per step, derived from the supplementary
  timing) lies in the region where the walk wins.
* **Distance remaining to the matched rho.** The matched Pollard rho with the negation map expects
  sqrt(pi n/4) steps; B3 needs 0.939 / 0.886 = about 1.0 to 1.1 times that (per-size column
  `B3 / sqrt(pi n/4)` below), the excess growing slowly with size in this range (distinguished-point
  overhead, which the v2 arm shares: its column is `A / sqrt(pi n/2)`). Against plain v2 rho the walk is
  at 0.700 of the steps.
* **Controls.** A/A (independent seeds) ratios contain 1 at every primary size (99% intervals);
  the positive control (the same walk without cycle handling) needs 11x and 76x the steps of A on
  the two small sizes, so the harness sees a large effect; the final source reproduces every earlier
  arm's stored output exactly (34 of 34 checks, `results/reproduction_gate.json`). All 8,600 solves
  returned the planted scalar; no solve gave up and no collision was degenerate.

## What happened, in order (read this before the numbers)

1. **Protocol frozen first** ([`PROTOCOL.md`](PROTOCOL.md), sha256 `4e8dc288eac7b7e0...`, hashes in
   `FREEZE.sha256`), then arms A, B, A2 (A/A control) and C (positive control) run: 200 chunks, all
   solves returned the planted scalar. **Pre-registered readout (arm B, the walk as first written):**
   pooled 0.717 (95% CI 0.689 to 0.747); step reduction verified at all four primary sizes
   (every interval's upper end is below 1); consistent with 0.707 at the three smaller sizes and
   *not* at 2^34.5 (0.784 [0.724, 0.851]).
2. **The frozen data showed a defect.** B/sqrt(pi n/4) rose with size (1.01, 1.04, 1.09, 1.21) and the
   escape count per step grew 20-fold. Tracing a lane (post hoc) found that the escape step, which
   had no 2-cycle look-ahead, undoes itself about once in 64 escapes; the escape is deterministic, so
   the lane then repeats it for the rest of the trail (162 of 300 solves at 2^34.5 contain a trapped trail).
   Trails are short at toy sizes (2^5 to 2^11 steps); a captured trail at the worker's density produces no
   distinguished point until the trail cap (32 x 2^d steps).
3. **Amendment 1** ([`PROTOCOL-AMENDMENT-1.md`](PROTOCOL-AMENDMENT-1.md), written before its data):
   arm B2 = escape step with the look-ahead and a jump no cycle step can have used. Pooled 0.701 (95% CI 0.674 to 0.729).
   Trapped-trail solves fell from 162 to 25 of 300 at 2^34.5 but did not vanish.
4. **Amendment 2** ([`PROTOCOL-AMENDMENT-2.md`](PROTOCOL-AMENDMENT-2.md), written before its data): a
   captured B2 lane showed the escape rejoining the cycle through a three-step reflection (any
   table-jump escape can). Arm B3 = escape by adding c*G, c from the cycle's smallest fingerprint,
   (a, b) -> (a + c, b), outside the span of the table jumps. **This is the shipped walk.**
   Trapped-trail solves: 0 of 1,800 (B: 252, B2: 29 of 1,800, each over about 164,000 trails).
   Pooled 0.700 (95% CI 0.673 to 0.728).
   What the diagnosis of B2's residue showed about *when* traps happen matters for production: they start
   early in a trail (ages 256 to 6,656 steps over 12 episodes in 120 solves), about 1 in 900 trails at 2^34.5
   for B2, and never in the 67 million steps of the long dp-free trails; so a captured trail costs its whole cap
   (32 x 2^d steps), not a per-step hazard. B3 had 0 captured of 22,224 trails at 2^34.5 (95% upper bound
   1.3e-4 per trail, about 8 times below B2).
5. B, B2 and B3 are reported side by side below; B2 and B3 are post hoc arms (prompted by seeing
   the data), B is the pre-registered one. The frozen verdict is the verdict of record for the
   question as posed; B3 answers whether the walk with the diagnosed defects removed reaches the
   quotient, and it does: its interval contains 1/sqrt(2) at all four primary sizes and at two of the three
   binary sizes (at 2^27 binary it is 0.632 [0.575, 0.696], slightly below).

Other deviations, all recorded where they happened:

* The first rate run passed the wrong step count (16x the frozen per-lane steps) because
  `run_study.py` multiplied by the lane count; 42 files were set aside unanalysed
  (`results/discarded/rate-16x-length-run/NOTE.md`), the argument was fixed to match PROTOCOL.md
  section 4 and the rate run repeated; the fixed `run_study.py` hash is in `FREEZE-AMENDMENT-2.sha256`.
  The long-trail check had the same bug and was fixed before it ran.
* The rate run of the frozen walk (arm B) never ran: it was queued behind other jobs on the heavy lock
  and cancelled when the defect was found; rate is reported for B3 only.
* The solve-phase `walk_ns` fields come from two concurrent driver processes (and, during the
  frozen run, from times when I was running unit tests). They are used only for the supplementary,
  post hoc per-step timing, which is labelled so; the frozen M2 numbers come from the separate
  single-process `rate` runs under the heavy-compute lock.
* The first solve run stopped at its 25-minute budget with 9 chunks left and was resumed
  (`results/solve/` is resumable by design); no other interruption in the measured runs.
* Several locked jobs waited for other agents' jobs for up to an hour; this affects only wall-clock time.
* `ec_core.h`, `negation_walk.py`, `codec.py` changed after the frozen run (amendments 1 and 2); the
  frozen sources are in `frozen-sources/` with SHA-256, and the reproduction gate shows the shipped
  source reproduces arms A, A2, B, B2 and C bit for bit.

## Measured values

### Mean steps per solve (lockstep steps; N solves per arm)

B = walk as first measured (frozen run), B2 = amendment 1, B3 = amendment 2 (the shipped walk).

| instance | log2 n | N (A/B/B2/B3) | mean A | mean B | mean B2 | mean B3 | A / sqrt(pi n/2) | B / sqrt(pi n/4) | B2 / sqrt(pi n/4) | B3 / sqrt(pi n/4) |
|---|---|---|---|---|---|---|---|---|---|---|
| toy-prime-24-0 | 23.05 | 300/300/300/300 | 3843 | 2629 | 2627 | 2618 | 1.042 | 1.008 | 1.007 | 1.004 |
| toy-prime-28-1 | 27.60 | 300/300/300/300 | 19374 | 13149 | 13019 | 13119 | 1.086 | 1.042 | 1.032 | 1.039 |
| toy-prime-31-2 | 30.87 | 300/300/300/300 | 58693 | 42694 | 41986 | 42549 | 1.058 | 1.088 | 1.070 | 1.085 |
| toy-prime-35-3 | 34.52 | 300/300/300/300 | 215387 | 168855 | 157996 | 154722 | 1.093 | 1.212 | 1.134 | 1.111 |
| toy-binary-25-0 | 24.00 | 200/200/200/200 | 5389 | 3558 | 3521 | 3474 | 1.050 | 0.980 | 0.970 | 0.957 |
| toy-binary-28-1 | 27.00 | 200/200/200/200 | 16072 | 10486 | 10357 | 10162 | 1.107 | 1.021 | 1.009 | 0.990 |
| toy-binary-33-2 | 32.00 | 200/200/200/200 | 91494 | 60363 | 58740 | 62211 | 1.114 | 1.039 | 1.011 | 1.071 |

### Step ratios to the v2 walk A, 95% bootstrap intervals (1/sqrt2 = 0.707)

| instance | B/A (frozen) | B2/A (amend. 1) | B3/A (amend. 2) | covers 1/sqrt2 (B, B2, B3) | B3/B2 | A2/A (99%) | C/A |
|---|---|---|---|---|---|---|---|
| toy-prime-24-0 | 0.684 [0.631, 0.742] | 0.683 [0.630, 0.742] | 0.681 [0.629, 0.740] | yes, yes, yes | 0.997 [0.919, 1.081] | 0.951 [0.859, 1.057] | 11.385 [10.020, 12.928] |
| toy-prime-28-1 | 0.679 [0.625, 0.737] | 0.672 [0.621, 0.730] | 0.677 [0.623, 0.734] | yes, yes, yes | 1.008 [0.926, 1.094] | 0.982 [0.884, 1.093] | 76.056 [67.479, 85.448] |
| toy-prime-31-2 | 0.727 [0.674, 0.786] | 0.715 [0.662, 0.772] | 0.725 [0.671, 0.780] | yes, yes, yes | 1.013 [0.940, 1.092] | 1.040 [0.936, 1.156] | - |
| toy-prime-35-3 | 0.784 [0.724, 0.851] | 0.734 [0.678, 0.793] | 0.718 [0.665, 0.776] | no, yes, yes | 0.979 [0.906, 1.058] | 1.000 [0.903, 1.109] | - |
| toy-binary-25-0 | 0.660 [0.597, 0.731] | 0.653 [0.590, 0.721] | 0.645 [0.582, 0.714] | yes, yes, yes | 0.987 [0.894, 1.088] | - | - |
| toy-binary-28-1 | 0.652 [0.592, 0.719] | 0.644 [0.583, 0.710] | 0.632 [0.575, 0.696] | yes, yes, no | 0.981 [0.887, 1.086] | - | - |
| toy-binary-33-2 | 0.660 [0.595, 0.729] | 0.642 [0.583, 0.709] | 0.680 [0.618, 0.748] | yes, yes, yes | 1.059 [0.957, 1.174] | - | - |

Pooled geometric mean of B/A (frozen) over the four primary sizes: 0.717 [0.689, 0.747]; verified step reduction at all four primary sizes.

Pooled geometric mean of B2/A (amendment 1, post hoc) over the four primary sizes: 0.701 [0.674, 0.729]; verified step reduction at all four primary sizes.

Pooled geometric mean of B3/A (amendment 2, post hoc) over the four primary sizes: 0.700 [0.673, 0.728]; verified step reduction at all four primary sizes.

Controls: A/A pass = True; positive control (C/A lower bound > 2) pass = True; A calibration in [0.8, 1.5] = True; reproduction gate (amended source reproduces the earlier arms) pass = True.

### Walk operations and trapped trails

| instance | adds/step B / B2 / B3 | escapes per 1e6 steps B / B2 / B3 | solves with a trapped trail (>200 escapes) B / B2 / B3 | trails completed (DPs + cap restarts), B3 | trail restarts at the cap per solve A / B / B2 / B3 / C |
|---|---|---|---|---|---|
| toy-prime-24-0 | 1.0158 / 1.0158 / 1.0225 | 411 / 261 / 265 | 0 / 0 / 0 | 24582 | 0.01 / 0.00 / 0.00 / 0.00 / 38.84 |
| toy-prime-28-1 | 1.0161 / 1.0161 / 1.0216 | 1390 / 232 / 229 | 10 / 0 / 0 | 30770 | 0.00 / 0.02 / 0.00 / 0.00 / 354.67 |
| toy-prime-31-2 | 1.0182 / 1.0161 / 1.0222 | 2715 / 355 / 257 | 39 / 2 / 0 | 24533 | 0.00 / 0.00 / 0.00 / 0.00 / - |
| toy-prime-35-3 | 1.0172 / 1.0166 / 1.0227 | 8396 / 1031 / 272 | 162 / 25 / 0 | 22224 | 0.00 / 0.01 / 0.00 / 0.00 / - |
| toy-binary-25-0 | 1.0160 / 1.0162 / 1.0235 | 1606 / 307 / 308 | 0 / 0 / 0 | 22142 | 0.03 / 0.03 / 0.00 / 0.00 / - |
| toy-binary-28-1 | 1.0158 / 1.0160 / 1.0210 | 1381 / 205 / 209 | 7 / 0 / 0 | 15953 | 0.00 / 0.00 / 0.00 / 0.00 / - |
| toy-binary-33-2 | 1.0156 / 1.0160 / 1.0222 | 3816 / 474 / 254 | 34 / 2 / 0 | 24200 | 0.00 / 0.03 / 0.00 / 0.00 / - |

### Long-trail trap check (16 lanes x 2^20 steps, no distinguished points)

Descriptive. Only 16 trails per run: B2's trapped trails start early in a trail (ages 256 to 6656 steps in the solve phase, about 1 in 900 trails at 2^34.5: 25 trapped solves in 22,720 trails), so this check cannot see them (expected 0.02 per run); it shows only that no lane entered a trap later in life. The evidence on trapping is the solve phase above.

| curve | arm | steps | escapes per 1e6 steps | lane-rounds with more than 12 escapes (trapped) | max escapes in one lane-round |
|---|---|---|---|---|---|
| toy-binary-33-2 | B2 | 16777216 | 200 | 0 | 2 |
| toy-binary-33-2 | B3 | 16777216 | 191 | 0 | 2 |
| toy-prime-35-3 | B2 | 16777216 | 255 | 0 | 2 |
| toy-prime-35-3 | B3 | 16777216 | 181 | 0 | 2 |

### Supplementary per-step timing from the solve phase (post hoc, not the frozen M2)

ns per step = walk time / physical steps over all solves of an arm (two driver processes at a time, arms interleaved in time; A2/A shows the noise level).

| instance | A ns/step | A2/A | B/A | B2/A | B3/A |
|---|---|---|---|---|---|
| toy-prime-24-0 | 5347 | 1.001 | 1.054 | 1.051 | 1.062 |
| toy-prime-28-1 | 6430 | 0.997 | 1.030 | 1.055 | 1.068 |
| toy-prime-31-2 | 7323 | 1.008 | 1.031 | 1.047 | 1.060 |
| toy-prime-35-3 | 8131 | 1.003 | 1.020 | 1.046 | 1.061 |
| toy-binary-25-0 | 3460 | - | 1.034 | 1.023 | 1.079 |
| toy-binary-28-1 | 4032 | - | 1.035 | 1.004 | 1.034 |
| toy-binary-33-2 | 4393 | - | 1.041 | 1.063 | 1.073 |

B3/A time per step over the 7 instances: mean 1.062, range 1.034 to 1.079.

### Priced comparison (group additions + lambda x native operations per solve; walk over A)

| instance | arm | ratio at lambda=0 | lambda=0.1 | lambda=1 | break-even lambda | order holds across 0..1 |
|---|---|---|---|---|---|---|
| toy-prime-24-0 | B | 0.695 | 0.820 | 1.384 | 0.28 | no |
| toy-prime-24-0 | B2 | 0.694 | 0.819 | 1.383 | 0.29 | no |
| toy-prime-24-0 | B3 | 0.696 | 0.822 | 1.385 | 0.28 | no |
| toy-prime-28-1 | B | 0.690 | 0.814 | 1.374 | 0.29 | no |
| toy-prime-28-1 | B2 | 0.683 | 0.806 | 1.360 | 0.31 | no |
| toy-prime-28-1 | B3 | 0.692 | 0.816 | 1.376 | 0.29 | no |
| toy-prime-31-2 | B | 0.741 | 0.874 | 1.475 | 0.21 | no |
| toy-prime-31-2 | B2 | 0.727 | 0.858 | 1.448 | 0.23 | no |
| toy-prime-31-2 | B3 | 0.741 | 0.874 | 1.474 | 0.21 | no |
| toy-prime-35-3 | B | 0.797 | 0.941 | 1.588 | 0.15 | no |
| toy-prime-35-3 | B2 | 0.746 | 0.880 | 1.485 | 0.21 | no |
| toy-prime-35-3 | B3 | 0.735 | 0.867 | 1.461 | 0.22 | no |
| toy-binary-25-0 | B | 0.671 | 0.792 | 1.336 | 0.33 | no |
| toy-binary-25-0 | B2 | 0.664 | 0.784 | 1.322 | 0.34 | no |
| toy-binary-25-0 | B3 | 0.660 | 0.778 | 1.312 | 0.35 | no |
| toy-binary-28-1 | B | 0.663 | 0.782 | 1.320 | 0.34 | no |
| toy-binary-28-1 | B2 | 0.655 | 0.773 | 1.304 | 0.36 | no |
| toy-binary-28-1 | B3 | 0.646 | 0.762 | 1.284 | 0.38 | no |
| toy-binary-33-2 | B | 0.670 | 0.791 | 1.335 | 0.33 | no |
| toy-binary-33-2 | B2 | 0.652 | 0.770 | 1.299 | 0.37 | no |
| toy-binary-33-2 | B3 | 0.695 | 0.820 | 1.383 | 0.29 | no |

### Rate of the shipped walk B3 vs A (CPU, single thread, dp_bits=40, 15 alternating runs)

| curve | ns/step A (IQR) | ns/step B3 (IQR) | B3/A time per step [95% CI] | rate loss per step | native overhead (GAE/step) | steps B3/A | net time B3/A |
|---|---|---|---|---|---|---|---|
| toy-prime-24-0 | 5745 (5628-5914) | 6297 (5507-6480) | 1.096 [0.954, 1.131] | 8.8% [-4.8, 11.6] | 0.090 | 0.681 | 0.747 [0.600, 0.837] |
| toy-prime-35-3 | 8724 (8229-8873) | 8716 (8326-9295) | 0.999 [0.952, 1.100] | -0.1% [-5.0, 9.1] | -0.017 | 0.718 | 0.718 [0.633, 0.854] |
| toy-binary-33-2 | 4663 (4392-4808) | 4702 (4496-5060) | 1.008 [0.955, 1.139] | 0.8% [-4.7, 12.2] | -0.007 | 0.680 | 0.686 [0.590, 0.852] |
| eccp-79 | 17312 (16468-17769) | 17736 (17248-18784) | 1.025 [0.978, 1.109] | 2.4% [-2.3, 9.8] | 0.008 | - | - |
| ecc2-79 | 10051 (9642-10538) | 10140 (9914-10242) | 1.009 [0.956, 1.056] | 0.9% [-4.6, 5.3] | -0.007 | - | - |


Long-trail trap check (amendment 2; 16 lanes x 2^20 steps, no distinguished points, rounds of 256):

- toy-binary-33-2 B2: 200 escapes per million steps, 0 trapped lane-rounds of 65536, max 2 escapes in one lane-round
- toy-binary-33-2 B3: 191 escapes per million steps, 0 trapped lane-rounds of 65536, max 2 escapes in one lane-round
- toy-prime-35-3 B2: 255 escapes per million steps, 0 trapped lane-rounds of 65536, max 2 escapes in one lane-round
- toy-prime-35-3 B3: 181 escapes per million steps, 0 trapped lane-rounds of 65536, max 2 escapes in one lane-round

## Campaign-level consequence

Measured constants (mean over the four primary prime sizes; they sit about 7% above theory at these toy sizes (distinguished-point lag, finite walk), so the tables below scale the theoretical E by the measured ratios): v2 mean steps = 1.341 sqrt(n) (theory sqrt(pi/2) = 1.253), shipped negation walk B3 0.939 sqrt(n) (theory sqrt(pi/4) = 0.886); earlier arms B 0.964, B2 0.940 sqrt(n). Steps ratio B3/A 0.700 (B/A 0.719, B2/A 0.701); time per step ratio 1.062 (mean of the seven solve-phase paired timings; the noisier dedicated runs on the 79-bit fields give 1.017); net time ratio 0.744.

| campaign | log2 E v2 (sqrt(pi n/2)) | log2 E negation (v2 E x measured steps ratio) | steps saved | log2 negation in v2-step time units | note |
|---|---|---|---|---|---|
| ecc2-79 | 39.33 | 38.81 | 30% | 38.90 |  |
| ecc2-89 | 44.33 | 43.81 | 30% | 43.90 |  |
| ecc2-97 | 48.33 | 47.81 | 30% | 47.90 |  |
| ecc2k-95 | 47.83 | 47.31 | 30% | 47.40 | Frobenius follow-up: log2 E = 44.03 |
| ecc2-109 | 54.33 | 53.81 | 30% | 53.90 |  |
| ecc2k-108 | 54.33 | 53.81 | 30% | 53.90 | Frobenius follow-up: log2 E = 50.44 |
| ecc2-131 | 65.33 | 64.81 | 30% | 64.90 |  |
| ecc2k-130 | 64.83 | 64.31 | 30% | 64.40 | Frobenius follow-up: log2 E = 60.81; production v1 sigma walk log2 E = 60.81 (the negation-only walk is worse here) |
| ecc2-163 | 81.33 | 80.81 | 30% | 80.90 |  |
| ecc2k-163 | 81.33 | 80.81 | 30% | 80.90 | Frobenius follow-up: log2 E = 77.15 |
| ecc2-191 | 95.33 | 94.81 | 30% | 94.90 |  |
| ecc2-238 | 118.83 | 118.31 | 30% | 118.40 |  |
| ecc2k-238 | 118.83 | 118.31 | 30% | 118.40 | Frobenius follow-up: log2 E = 114.38 |
| ecc2-353 | 176.70 | 176.19 | 30% | 176.28 |  |
| ecc2k-358 | 179.33 | 178.81 | 30% | 178.90 | Frobenius follow-up: log2 E = 174.58 |
| eccp-79 | 39.64 | 39.13 | 30% | 39.21 |  |
| eccp-89 | 44.54 | 44.03 | 30% | 44.11 |  |
| eccp-97 | 48.58 | 48.07 | 30% | 48.16 |  |
| eccp-109 | 54.73 | 54.21 | 30% | 54.30 |  |
| eccp-131 | 65.42 | 64.91 | 30% | 64.99 |  |
| eccp-163 | 81.50 | 80.99 | 30% | 81.07 |  |
| eccp-191 | 95.81 | 95.30 | 30% | 95.39 |  |
| eccp-239 | 119.81 | 119.29 | 30% | 119.38 |  |
| eccp-359 | 179.56 | 179.05 | 30% | 179.14 |  |

| v2 work done (fraction of E_v2) | v2 remaining (units of E_v2) | negation walk from scratch (steps) | (time) |
|---|---|---|---|
| 0.00 | 1.000 | 0.700 | 0.744 |
| 0.05 | 0.952 | 0.700 | 0.744 |
| 0.10 | 0.907 | 0.700 | 0.744 |
| 0.20 | 0.828 | 0.700 | 0.744 |
| 0.30 | 0.759 | 0.700 | 0.744 |
| 0.50 | 0.646 | 0.700 | 0.744 |
| 1.00 | 0.461 | 0.700 | 0.744 |

Switching a campaign to the negation walk lowers the remaining expected steps only while v2 has done less than 0.40 of its E_v2, and the remaining time while less than 0.32.


Remaining expected work per campaign, using the measured constants: under v2 it is the full
E_v2 (the work done so far is far below 1e-4 of E on every curve: the 2026-10-19 snapshot in
`verification/hardware-20261019/public-status.json` has 560 verified v2 points in total, 535 of them on
ECCp-79, about 535 x 2^16 = 3.5e7 steps against E = 2^39.6, which is 4e-5, and ECCp-79 is solved
anyway); under the negation walk it is **0.700 E_v2 in steps, 0.744 E_v2 in v2-step time** (the table
above gives log2 of each per campaign). A negation-walk namespace starts empty and its points never combine
with v2 points (nor with ECC2K-130's 1.68 million v1 sigma-walk points), so v2 work already done is not
credited: the switch pays only while v2 has done less than about 0.40 of its E (steps) or 0.32 (time), per
the progress table. The exception is ECC2K-130, which already runs the v1 walk with the Frobenius quotient
(E = sqrt(pi n/(4 x 131)), better than negation alone by a factor sqrt(131) = 11.4); the follow-up that would
bring the other Koblitz campaigns (ECC2K-95, -108, -163, -238, -358) a further sqrt(m) is the Frobenius-class
design in `docs/negation-walk.md`, not implemented here.

## Limits and what would change them

* Toy curves, group orders 2^24 to 2^36: the effect of distinguished-point overhead and of trail length at
  production DP densities (trails of about 2^34 steps) is not measured. The long-trail check (16 lanes x 2^20
  steps, no DPs) saw no trapped lane for B2 or B3, but it has too few trails (16 per run) to see
  early-capture traps (B2 expected 0.02 per run), so it is not evidence about B3; it shows only that no
  lane fell into a trap later in life (the long-trail check's 67 million steps saw none). The evidence on trapping is the solve phase (0 of 164,404 B3 trails against 29
  trapped solves for B2). Before enabling the walk, run a few hundred thousand trail starts at the
  production DP density on a real curve with the trapped-lane counter of `solve_bench rate`.
* GPU: untested. The CUDA kernel calls `walk_lane_any`; stack use (a 256-byte cycle window plus a
  few points per lane) and the 8 KiB device stack limit were not checked on a device.
* Rates are those of this project's generic bit-serial 12-word arithmetic on a shared VM.
* `walk_lane_neg` depends on the round size (the cycle window restarts each call); the namespace
  does not pin it, so server-side replay is not offered (the verifier checks each record
  algebraically). Pinning `steps_per_round` in the manifest would make replay exact.
* Frobenius classes for Koblitz campaigns (a further sqrt(m)): not implemented; design in
  `docs/negation-walk.md`.

## Files

* Protocol and evidence: `PROTOCOL.md`, `PROTOCOL-AMENDMENT-1.md`, `PROTOCOL-AMENDMENT-2.md`,
  `FREEZE.sha256`, `FREEZE-AMENDMENT-1.sha256`, `FREEZE-AMENDMENT-2.sha256`, `frozen-sources/`,
  `results/` (raw `solve/*.jsonl`, `rate/*.json`, `longtrail/*.json`, `summary.json`, `tables.md`,
  `campaign_saving.{json,md}`, `reproduction_gate*.json`, `env*.json`, `discarded/`).
* Scripts: `run_study.py` (driver orchestration), `reproduction_gate.py`, `analyze.py`,
  `campaign_saving.py`, `figure.py`, `make_report.py` (the figure and PDF use only recorded values).
* Code under test: `worker/generic/ec_core.h` (`walk_lane_neg`), `worker/generic/solve_bench.cpp`
  (driver, not shipped), `src/ecc_challenges/multicurve/negation_walk.py` (reference),
  `tests/fixtures/toy_instances.json` (planted curves).

Reproduce (each locked job under `flock`): `python3 run_study.py build --work W`, then `solve`, `rate`,
`longtrail`, `env`; `python3 reproduction_gate.py --work W`; `python3 analyze.py`;
`python3 campaign_saving.py`; `python3 figure.py`; `python3 make_report.py`.
