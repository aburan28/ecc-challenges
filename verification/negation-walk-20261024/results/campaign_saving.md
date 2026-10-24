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
