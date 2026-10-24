## Mean steps per solve (lockstep steps; N solves per arm)

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

## Step ratios to the v2 walk A, 95% bootstrap intervals (1/sqrt2 = 0.707)

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

## Walk operations and trapped trails

| instance | adds/step B / B2 / B3 | escapes per 1e6 steps B / B2 / B3 | solves with a trapped trail (>200 escapes) B / B2 / B3 | trails completed (DPs + cap restarts), B3 | trail restarts at the cap per solve A / B / B2 / B3 / C |
|---|---|---|---|---|---|
| toy-prime-24-0 | 1.0158 / 1.0158 / 1.0225 | 411 / 261 / 265 | 0 / 0 / 0 | 24582 | 0.01 / 0.00 / 0.00 / 0.00 / 38.84 |
| toy-prime-28-1 | 1.0161 / 1.0161 / 1.0216 | 1390 / 232 / 229 | 10 / 0 / 0 | 30770 | 0.00 / 0.02 / 0.00 / 0.00 / 354.67 |
| toy-prime-31-2 | 1.0182 / 1.0161 / 1.0222 | 2715 / 355 / 257 | 39 / 2 / 0 | 24533 | 0.00 / 0.00 / 0.00 / 0.00 / - |
| toy-prime-35-3 | 1.0172 / 1.0166 / 1.0227 | 8396 / 1031 / 272 | 162 / 25 / 0 | 22224 | 0.00 / 0.01 / 0.00 / 0.00 / - |
| toy-binary-25-0 | 1.0160 / 1.0162 / 1.0235 | 1606 / 307 / 308 | 0 / 0 / 0 | 22142 | 0.03 / 0.03 / 0.00 / 0.00 / - |
| toy-binary-28-1 | 1.0158 / 1.0160 / 1.0210 | 1381 / 205 / 209 | 7 / 0 / 0 | 15953 | 0.00 / 0.00 / 0.00 / 0.00 / - |
| toy-binary-33-2 | 1.0156 / 1.0160 / 1.0222 | 3816 / 474 / 254 | 34 / 2 / 0 | 24200 | 0.00 / 0.03 / 0.00 / 0.00 / - |

## Long-trail trap check (16 lanes x 2^20 steps, no distinguished points)

Descriptive. Only 16 trails per run: B2's trapped trails start early in a trail (ages 256 to 6656 steps in the solve phase, about 1 in 900 trails at 2^34.5: 25 trapped solves in 22,720 trails), so this check cannot see them (expected 0.02 per run); it shows only that no lane entered a trap later in life. The evidence on trapping is the solve phase above.

| curve | arm | steps | escapes per 1e6 steps | lane-rounds with more than 12 escapes (trapped) | max escapes in one lane-round |
|---|---|---|---|---|---|
| toy-binary-33-2 | B2 | 16777216 | 200 | 0 | 2 |
| toy-binary-33-2 | B3 | 16777216 | 191 | 0 | 2 |
| toy-prime-35-3 | B2 | 16777216 | 255 | 0 | 2 |
| toy-prime-35-3 | B3 | 16777216 | 181 | 0 | 2 |

## Supplementary per-step timing from the solve phase (post hoc, not the frozen M2)

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

## Priced comparison (group additions + lambda x native operations per solve; walk over A)

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

## Rate of the shipped walk B3 vs A (CPU, single thread, dp_bits=40, 15 alternating runs)

| curve | ns/step A (IQR) | ns/step B3 (IQR) | B3/A time per step [95% CI] | rate loss per step | native overhead (GAE/step) | steps B3/A | net time B3/A |
|---|---|---|---|---|---|---|---|
| toy-prime-24-0 | 5745 (5628-5914) | 6297 (5507-6480) | 1.096 [0.954, 1.131] | 8.8% [-4.8, 11.6] | 0.090 | 0.681 | 0.747 [0.600, 0.837] |
| toy-prime-35-3 | 8724 (8229-8873) | 8716 (8326-9295) | 0.999 [0.952, 1.100] | -0.1% [-5.0, 9.1] | -0.017 | 0.718 | 0.718 [0.633, 0.854] |
| toy-binary-33-2 | 4663 (4392-4808) | 4702 (4496-5060) | 1.008 [0.955, 1.139] | 0.8% [-4.7, 12.2] | -0.007 | 0.680 | 0.686 [0.590, 0.852] |
| eccp-79 | 17312 (16468-17769) | 17736 (17248-18784) | 1.025 [0.978, 1.109] | 2.4% [-2.3, 9.8] | 0.008 | - | - |
| ecc2-79 | 10051 (9642-10538) | 10140 (9914-10242) | 1.009 [0.956, 1.056] | 0.9% [-4.6, 5.3] | -0.007 | - | - |
