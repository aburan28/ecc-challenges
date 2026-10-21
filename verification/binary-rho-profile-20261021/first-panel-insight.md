# CUDA bit-length first L4 panel, 2026-10-21

Observation report for a bounded worker benchmark. It assigns no research
status or full campaign conclusion. The report's quantitative
[graph and PDF](reports/l4-run-1/report.pdf) are derived from the same receipt;
the [editable plot source](render_panel.py),
[SVG](reports/l4-run-1/ratio.svg), and
[PDF source](reports/l4-run-1/report.typ) are retained.

Protocol: [frozen plan](plan.json). Raw measurements and generated jobs:
[receipt](l4-run-1/receipt.json), [archive](l4-run-1/raw.tar.gz), and
[exact launcher](l4-run-1/launcher.py). Replay with
`python3 verification/binary-rho-profile-20261021/verify_profile.py verification/binary-rho-profile-20261021/l4-run-1`.

The baseline header is SHA-256
`90b2d04da9c626e2d0a0f6e63f3f50929a93b28caf522211bfdae43b414eff80`;
the candidate header is
`b20f3ae4e47ba25576a4353d374a131333f1b8584c42ca39925baf24c381999a`.
Both arms used the same instrumented engine and the frozen nine-curve control
archive. The selected L4 was reported as NVIDIA L4, driver 610.57.04. The
compile target was `compute_80` PTX, so JIT codegen was not captured as a native
register/instruction report.

The run used checkout base `98a2527` with uncommitted candidate, engine timing,
and launcher changes. Command: `modal run scripts/bench_modal_binary_rho.py
--lanes 256 --steps 512 --repeats 2`, with `ECC_BASELINE_HEADER` pointing to
`baseline-ec_core.h`, `ECC_NATIVE_CONTROLS` to the extracted archived controls,
and `ECC_BENCH_OUT` to `l4-run-1`. The exact per-curve jobs, expanded input
states and their embedded seeds are in the raw archive. Fifty-four invocations
were requested and completed; zero failed or timed out. The host and
full-process wall times are in the receipt. Per-invocation timestamps and
separate CPU time were not captured.

The replay verified 54 successful worker invocations, 18 matched
baseline–candidate–baseline comparisons, the full `256 * 512` actual steps in
each invocation, and exact output and distinguished-point hashes within every
comparison. The median candidate/baseline full-process ratio was 0.834 across
all 18 comparisons. Restricting to 12 comparisons whose baseline bracket drift
was at most 1.05, the median ratio was 0.824; 11 favored the candidate. The
median kernel-event ratio across all 18 was 0.732. The first ECC2K-130 pair
had severe baseline drift (3.73) and reversed the signal; one stable ECC2-131
pair had a full-process ratio of 1.025 despite a faster kernel.

The observed ratios in this bounded panel motivated a native L4 replication
and A/A control, specified before those runs in
[the follow-up plan](native-replication-plan.json) before selecting the change.
The panel does not include sustained point collection, network submission,
verification, or full campaign cost.
