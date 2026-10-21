# Native L4 binary-rho arithmetic panel, 2026-10-21

Observation report for the native A/B benchmark. It assigns no research status
or complete-campaign conclusion. Source artifacts: [frozen plan](native-replication-plan.json),
[raw receipt](native-ab/receipt.json), [raw archive](native-ab/raw.tar.gz),
[launcher](native-launcher.py), [graph](reports/native-ab/ratio.svg),
[editable graph source](render_panel.py), [PDF source](reports/native-ab/report.typ),
and [inspected PDF](reports/native-ab/report.pdf).

Command: `modal run scripts/bench_modal_binary_rho.py --lanes 256 --steps 512
--repeats 2`, with `ECC_CUDA_GENCODE=arch=compute_89,code=sm_89`, original
header as `ECC_BASELINE_HEADER`, the extracted frozen control archive as
`ECC_NATIVE_CONTROLS`, and `native-ab` as `ECC_BENCH_OUT`. The run used the
uncommitted native-launcher revision atop commit `a970f4a`; the exact launcher
is archived above. Both arms used the same instrumented engine SHA-256
`87211d525a18469c59e96fde2461d66d38c22a9b0a2362744ab8e82173443684`.
The baseline and candidate header hashes are respectively
`90b2d04da9c626e2d0a0f6e63f3f50929a93b28caf522211bfdae43b414eff80`
and `b20f3ae4e47ba25576a4353d374a131333f1b8584c42ca39925baf24c381999a`.
The selected device was NVIDIA L4 with driver 610.57.04. Per-curve jobs,
expanded states and their embedded seeds are retained in the raw archive.
The receipt contains device, environment, source/binary hashes, command per
invocation, timing and compiler output. CPU time and individual invocation
timestamps were not captured.

Requested/completed: 54/54 worker invocations and 18/18 matched comparisons;
zero failures or timeouts. The independent replay verified exact output and
distinguished-point hashes, and `256 * 512` actual steps for every invocation.
All 18 A/B full-process ratios were below 1, with median 0.824. Thirteen
baseline brackets had drift at most 1.05; all 13 favored the candidate and
their median candidate/baseline full-process ratio was 0.839. The median
kernel-event ratio across 18 pairs was 0.730. Across individual invocations,
median full-process times were 579.6 ms for baseline and 471.0 ms for
candidate; median kernel times were 366.1 ms and 265.0 ms. Paired ratios are
the comparison metric, since absolute medians do not preserve bracket order.

Native compiler logs and `cuobjdump` report 159 registers for the baseline
kernel and 160 for the candidate, with the same 1552-byte stack frame and no
reported spills. This panel records worker invocation and kernel time only;
collector, network, point verification and sustained campaign costs remain
outside its scope. The independently specified A/A control is a separate run.
