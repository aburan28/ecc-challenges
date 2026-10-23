# Optimized CUDA positive-DP control, 2026-10-21

Observation report for the finite correctness run. It assigns no research
status or campaign throughput conclusion. Artifacts:
[GPU receipt](positive-dp-cuda-receipt.json),
[exact launcher](positive-dp-launcher.py),
[independent receipt verifier](verify_positive_dp.py),
[graph](reports/positive-dp/dp1-counts.svg),
[editable graph source](render_positive_dp.py),
[PDF source](reports/positive-dp/report.typ), and
[inspected PDF](reports/positive-dp/report.pdf).

Command: `ECC_NATIVE_CONTROLS=<extracted frozen archive> modal run
scripts/check_modal_curves.py` on checkout head `7628667`, with the generated
site-doc fix and this verifier uncommitted; neither changes the compiled worker.
The frozen input/CPU-reference archive is
`verification/all-curves-20261016/xtime-20261016/cpu-controls.tar.gz`, SHA-256
`ce502b916d286a7204430e598f4117ea1c17e8dfb4f6aa991a4edf1168ef4629`.
The optimized arithmetic header has SHA-256
`b20f3ae4e47ba25576a4353d374a131333f1b8584c42ca39925baf24c381999a`;
the receipt records the exact engine and binary hashes. Modal selected an
NVIDIA L4 with driver 610.57.04. The launcher compiled with CUDA 13.0.2,
`-O2`, and `arch=compute_80,code=compute_80`, then ran its self-test. No
contributor key, production run or checkpoint was used.

Requested/completed: 168/168 controls across 24 curves, with no failed or
timed-out case. The launcher compared CUDA output and DP bytes to the frozen
CPU reference for successful cases and checked malformed-input rejections.
Independent replay checked all returned input, state and DP hashes against
the frozen archive, the source hash, and nonempty DP1 output on every curve.
The nine requested binary curves had 137 expected and GPU-matched DP1 records.
Wall and CPU time per control were not captured. The launcher returned hashes
rather than raw CUDA output files; the receipt verification checks those hashes
and the launcher's comparison result, with that retention limit explicit.

The three long DP40 timing panels emitted zero DP records. Their identical DP
hashes therefore checked empty outputs. This DP1 run is the separate positive
emission check for the optimized source. Finite DP1 emission does not measure
long-run DP40 density, point submission or campaign throughput.
