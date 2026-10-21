# Native L4 identical-arithmetic control, 2026-10-21

Observation report for the A/A worker benchmark. It assigns no research status
or complete-campaign conclusion. Artifacts: [frozen plan](native-replication-plan.json),
[raw receipt](native-aa/receipt.json), [raw archive](native-aa/raw.tar.gz),
[launcher](native-launcher.py), [graph](reports/native-aa/ratio.svg),
[editable graph source](render_panel.py), [PDF source](reports/native-aa/report.typ),
and [inspected PDF](reports/native-aa/report.pdf).

Command: `modal run scripts/bench_modal_binary_rho.py --lanes 256 --steps 512
--repeats 2`, with `ECC_CUDA_GENCODE=arch=compute_89,code=sm_89`, the frozen
candidate header as `ECC_BASELINE_HEADER`, the same archived controls as
`ECC_NATIVE_CONTROLS`, and `native-aa` as `ECC_BENCH_OUT`. The run used branch
commit `3ebaced`. Both arms had the same header SHA-256
`b20f3ae4e47ba25576a4353d374a131333f1b8584c42ca39925baf24c381999a`,
the same engine SHA-256
`87211d525a18469c59e96fde2461d66d38c22a9b0a2362744ab8e82173443684`,
and the same binary SHA-256
`5d50279dc702eb45196aeba930df6c6f72000644f79ef618500c098b824235f5`.
The selected device was NVIDIA L4 with driver 610.57.04. Per-curve jobs,
expanded states and their embedded seeds are retained in the raw archive.
The receipt contains the host, source/binary hashes, per-invocation commands,
full-process and CUDA event times, and native compiler output. Separate CPU
time and per-invocation timestamps were not captured.

Requested/completed: 54/54 invocations and 18/18 matched comparisons, with
zero failures or timeouts. Independent replay verified exact output and
distinguished-point hashes and `256 * 512` steps for every invocation. The
all-pair median A/A full-process ratio was 0.999, spanning 0.966–1.022.
Sixteen brackets had drift at most 1.05; their median ratio was 1.004,
spanning 0.971–1.022. The all-pair median kernel-event ratio was 1.000,
spanning 0.998–1.001. The native resource report was identical in both arms:
160 registers, 1552-byte stack frame and no reported spills.

These are invocation and kernel timing controls for the specified L4 workload.
They do not measure network submission, collector behavior, verification or
sustained campaign cost.
