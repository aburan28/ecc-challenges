# Tools

## Progress metrics (daily lab report)

`progress_metrics.py` exports this repository's progress metrics as JSONL for
the lab-wide daily report. It reads the committed challenge-instance registry
at `origin/main` (override with `--ref` or `PM_REF`) and prints one sample per
line: `metric, value, date, problem, unit, better, kind, label, source`.

- If a PR adds or changes a benchmark, leaderboard, result file or ledger
  source with a number worth tracking day over day, extend the exporter in the
  same PR. Keep metric ids stable; a renamed id is a new series.
- Never emit estimates. Omit expected rho work, operation counts and rates
  derived from distinguished points (DPs), and projected completion times. If a
  source is missing, emit nothing for it; a missing walk rate is not a measured
  zero. Omit non-finite values. Iteration totals counted from worker
  checkpoints stay in, because they do not depend on DP density.
- Live gateway counts and local ingest snapshots are separate from committed
  repository data. Snapshot samples identify the source filename and SHA-256.
  An invalid Git reference is an error.
- `python3 tools/progress_metrics.py --since 2026-10-01` must finish in under a
  minute, use only the standard library, run no builds and never read
  repository data from the working tree. The optional public gateway request
  has a 25-second total deadline, and an unavailable gateway does not block
  local exports.
