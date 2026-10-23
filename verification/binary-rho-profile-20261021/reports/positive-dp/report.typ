#set page(width: 11in, height: 8.5in, margin: 0.65in)
#set text(size: 10pt)
= Optimized CUDA positive-DP control

Observation record. GPU receipt: `positive-dp-cuda-receipt.json`.
Frozen controls: `cpu-controls.tar.gz`, SHA-256
`ce502b916d286a7204430e598f4117ea1c17e8dfb4f6aa991a4edf1168ef4629`.
The checker compared GPU output and DP bytes against the frozen CPU cases;
the independent replay checks receipt hashes and requires positive DP1 output.

#image("dp1-counts.svg", width: 100%)

All 168 GPU checks completed across 24 curves. Each curve emitted at least one
DP1 record in its positive control. The nine requested binary curves contributed
137 expected records, with exact GPU equality checked by the launcher. The
three DP40 timing panels emitted zero records, so this is their separate
positive-DP correctness control. No long-run DP density or campaign throughput
is inferred from these finite cases.
