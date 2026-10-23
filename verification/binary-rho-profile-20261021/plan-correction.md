# Plan hash correction, 2026-10-21

The frozen [first-panel plan](plan.json) labels
`391d46a725d2dc7d63a9eb9bfe94eaf863c83074f54353dcecc9e51d28fcb13e`
as `baseline_engine_sha256`. That is the uninstrumented engine at the base
commit, before the common CUDA event timing code was added. The actual
instrumented engine uploaded to **both** arms has SHA-256
`87211d525a18469c59e96fde2461d66d38c22a9b0a2362744ab8e82173443684`.
The [receipt](l4-run-1/receipt.json) records that actual source hash in both
arms and the binaries' hashes. This correction preserves the original plan
unchanged and changes no workload, comparison, or observed result.
