# PROTOCOL-AMENDMENT-2 (written before any arm-B3 data)

Date 2026-10-24. Like amendment 1 it adds an arm and changes none of the frozen
rules or results. The frozen verdict of `PROTOCOL.md` and the amendment-1 readout
stand as recorded.

## 1. What amendment 1's data showed

Arm B2 (escape by a table jump with look-ahead, `results/solve/*.B2.*`) removed
the self-undoing escape: B2/A at 2^34.5 fell from 0.784 to 0.734 and the solves
with a trapped trail (more than 200 escapes) from 162 to 25 of 300. But 25 of 300
remain, B2/sqrt(pi n/4) still rises with size (1.007, 1.032, 1.070, 1.134 over
the four prime sizes), and the per-step escape count at 2^34.5 (1031 per million)
is about four times the cycle hazard seen elsewhere (about 260 per million).

## 2. Diagnosis of the residue (post hoc)

The lane of solve 23 (toy-prime-35-3, 8 lanes, round 37) was captured and replayed
in the reference implementation. It sits on a 4-cycle between partitions 17 and
26; the escape (table jump 1, the first unused jump after 17 + 16) lands on E
with partition 26, E steps by jump 26 to E2 with partition 1, and E2 steps by
jump 1 back onto the cycle (M -> E -> E2 -> M). The signs make the three steps
cancel: with E = -(M + T1), E2 = E + T26 and the cycle's own relation
P2 = +-(P1 + T26), E2 + T1 = -P2 - T26, a cycle member. Any escape that adds a
table jump can be undone by such a reflection pattern (the escape jump, a jump
the cycle uses, the escape jump again), about once in several hundred cycles; the
escape is deterministic, so the walk then repeats it forever.

## 3. The amendment

* Escape by a point outside the span of the table jumps: add c*G with
  c = 1 + (fingerprint(M) >> 5) mod 65535, (a, b) -> (a + c, b), M the cycle's
  smallest-fingerprint member. The table jumps are random multiples of G with
  full-width coefficients, so no path of table jumps can sum to -c*G (it would
  need a relation among random scalars), the walk cannot return into the cycle,
  and b is unchanged (the trail-start binding survives). Cost: about 24 point
  additions per escape (double-and-add over 16 bits), counted in the adds.
* Source (`worker/generic/ec_core.h`, `negation_walk.py`, `codec.py` manifest
  text): `job.reserved == 1` is now this walk; 2 is the ablation; 3 is the frozen
  arm B (table-jump escape without look-ahead) and 4 is arm B2 (table-jump escape
  with look-ahead), both kept so every measured arm stays reproducible from the
  shipped source. The engine's file parser accepts only 1. The sources of the
  frozen run and of amendment 1 are in `frozen-sources/`.
* Reproduction gate (`reproduction_gate.py`): the new driver must reproduce the
  stored output for A, A2 (mode 0), C (mode 2), B (mode 3) and B2 (mode 4) on the
  first five solves of the first chunk of every instance; if it fails no
  amendment-2 arm counts. (Amendment 1's gate result is kept as
  `results/reproduction_gate_amendment1.json`.)
* Development check before this text (not part of the measurement, other seed):
  toy-prime-35-3, seed 777, 40 solves per arm: trapped trails 18 (mode 3), 3
  (mode 4), 0 (mode 1), mean steps 159586, 153279, 157998 (not compared; noise
  is about 6% at 40 solves). The measurement seeds are not used for development.
* New arm **B3** (`job.reserved == 1`): every parameter of the frozen B (instances,
  solves per arm, seeds, lanes, round size, DP-bit rule with E_B, trail cap,
  give-up limit), same planted d and start coefficients per solve index.
* Rules for B3: M1 to M5 and the controls of `PROTOCOL.md` with B3 in place of B,
  as for B2 in amendment 1; plus B3/B2 with a 95% interval, the share of solves
  with a trapped trail and the trail restarts at the cap per solve.
* Long-trail check (new, descriptive): for toy-prime-35-3 and toy-binary-33-2,
  arms B2 and B3, 16 lanes of 2^20 steps each, no distinguished points, rounds of
  256 steps, seed 11. Report escapes per million steps, the lane-rounds with
  more than 12 escapes (trapped), and the largest escape count in one lane-round.
  Its purpose is to look for trap loops at trail lengths 500 times the study's.
* Rate (unchanged parameters of `PROTOCOL.md` section 4): arms A (mode 0) and B3
  (mode 1). The rate run queued before this amendment was cancelled before it
  produced a byte; B3 is the shipped walk, so B3 is the walk whose rate is
  reported.
* Status: B3 is the third post-hoc arm, prompted by amendment 1's data. The
  frozen readout, B2 and B3 are all reported side by side. B3 is the
  walk the code ships; whether it reaches 0.707 at every size is a result, not a
  premise, and a residual trapped-trail count above zero would be reported as
  such.
