Discarded rate run (42 files). run_study.py passed `per_lane * lanes` to
`solve_bench rate`, whose STEPS argument is per lane, so every run walked 16
times the 16384 (toy) or 4096 (real) steps per lane that PROTOCOL.md section 4
freezes. The first repetitions took 23 to 35 s each (about 70 minutes of exclusive
lock time for the whole run); the run was stopped and the files set aside, not
analysed. The argument was corrected to pass the frozen per-lane step count and
the rate run was repeated from scratch. The ns/step values in these files
(toy-prime-24-0 A 5520 B3 5636; toy-prime-35-3 A 8172 B3 8401, first repetition) are
of the same order as the rerun's.
