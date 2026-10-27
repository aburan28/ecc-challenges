# PROTOCOL-AMENDMENT-1 (written before any amended-arm data)

Date 2026-10-24. This amendment adds an arm; it changes none of the frozen
rules in `PROTOCOL.md` and none of the frozen results.

## 1. What the frozen run showed

The frozen solve run (arms A, B, A2, C; `results/solve/`) finished before any
rate data existed. Its pre-registered readout (`results/tables.md` as of that
run) shows B/A near 0.68 at the two smallest prime sizes rising to 0.727 and
0.784 at 2^30.9 and 2^34.5, B/sqrt(pi n/4) rising 1.01, 1.04, 1.09, 1.21, and a
count of escapes that grows with size (8396 per million steps at 2^34.5; the
per-step cycle hazard is about 400 per million). That pattern is not
predicted by the design. It is recorded here as seen, not explained away.

## 2. Diagnosis (post hoc, not part of the frozen analysis)

Tracing a lane with more than 300 escapes in 4096 steps (toy-prime-35-3) showed
that the escape step, which had no 2-cycle look-ahead, lands on a point E with
the sign flipped and E's own partition equal to the escape jump, so the next
step returns to the cycle member M it left (M -> E -> M: the escape undoes
itself, about 1 escape in 64). The escape is a deterministic function of the
cycle, so the walk then repeats it for the rest of the trail. Trails are short
at small sizes and long at large ones, so the loss grows with size; in
production (trails of about 2^34 steps) such a lane would be lost for good.
Raw evidence: 162 of the 300 B solves at 2^34.5 contain more than 200 escapes
(the v2 walk has no escapes by construction).

## 3. The amendment

* Source change (`worker/generic/ec_core.h`, `src/.../negation_walk.py`,
  `codec.py` manifest text): the escape step is taken with the same 2-cycle
  look-ahead as every other step. `job.reserved == 3` keeps the walk exactly as
  first measured (escape step without look-ahead) so the frozen arm stays
  reproducible from the amended source; the engine file parser never accepts
  2 or 3. Pre-amendment sources are in `frozen-sources/` with their SHA-256.
* Reproduction gate (`reproduction_gate.py`, `results/reproduction_gate.json`):
  the amended driver must reproduce the stored frozen output (all fields except
  `walk_ns` and the mode label) for A and A2 (mode 0), C (mode 2) and the frozen
  B (mode 3) on the first five solves of the first chunk of every instance.
  Result: pass, 20 of 20 checks. If it had failed, no amended arm would count.
* New arm **B2**: the amended walk (`job.reserved == 1`), on every instance and
  with every parameter of the frozen B (solves per arm and instance, seeds,
  lanes, round size, DP-bit rule with E_B, trail cap, give-up limit). B2 uses
  the same planted d and the same start coefficients as A and the frozen B for
  each solve index. The frozen arm is reported as "B (frozen, as measured)"
  and is never replaced.
* Rules for B2: M1 to M5 and the controls of `PROTOCOL.md` section 6 apply
  with B2 in place of B (the A/A and positive controls and the calibration are
  the frozen ones, unchanged). In addition: the ratio mean(B2)/mean(B) with a
  95% bootstrap interval per instance, the fraction of solves with more than 200
  escapes, and the trail restarts at the cap per solve, for B and B2.
* Rate (`PROTOCOL.md` section 4, unchanged parameters): arms A (mode 0) and B2
  (mode 1) only. The rate run queued before the amendment was cancelled before
  it produced a byte, so no rate data exists for the frozen walk.
* Status of results: B2 is a post-hoc amendment prompted by seeing the frozen
  data. Every table and the README label it so and show the frozen B result
  next to it. The frozen verdict (`PROTOCOL.md` section 5) is the verdict of
  record for the pre-registered question; B2 answers a second question,
  whether the walk with the diagnosed defect removed reaches the quotient.
