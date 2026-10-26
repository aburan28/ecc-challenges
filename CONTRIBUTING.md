# Contributing

There are two ways to help: run a worker, or work on the code.

## Run a worker

Sign in at [ecc-challenges.com](https://ecc-challenges.com), create an API key
and run the `docker run` command your dashboard shows.
[Running a worker](docs/running-a-worker.md) covers every other setup:
CPU-only, Compose, Kubernetes, systemd, Podman, Slurm and Modal. If something
looks wrong, such as rejected uploads, a crash or a number on the status page
that does not add up, [open an issue](https://github.com/isogeny-labs/ecc-challenges/issues/new/choose)
with the worker log. Never paste your API key.

## Work on the code

### Layout

| Path | What it is |
| --- | --- |
| `src/ecc_challenges/` | FastAPI website and worker API (`app.py`), templates, status page |
| `src/ecc_challenges/multicurve/` | v2 protocol for all 24 curves: codec, admission, verification, index |
| `worker/` | Worker client and generic CPU/CUDA/Metal engine. `generic/ec_core.h` is the shared reference core; `generic/fast131.h` is the specialized 131-bit arithmetic and batched walks; `CMakeLists.txt` builds both on Linux, macOS and Windows. `Dockerfile` (GPU) and `Dockerfile.cpu` (CPU, amd64 and arm64) build the images. `tests/` runs on every platform with only pytest |
| `deploy/` | Ways to run workers (Compose, Kubernetes, systemd, Podman, Slurm) and the local stack |
| `verifier/` | Replay and verification code used by the audit image |
| `sql/` | PostgreSQL migrations, applied in order |
| `infra/` | CloudFormation stacks |
| `scripts/` | Deploy, publish and live-check scripts, most of which need AWS credentials |
| `pool/` | Separate small-curve pool and STARK prototype with its own CI |
| `problems/` | Open problems with the evidence needed to close each one |
| `verification/` | Committed receipts from release checks |

### Set up and run the tests

You need Python 3.12+, [uv](https://docs.astral.sh/uv/) and PostgreSQL 16.

```sh
uv sync --extra test
uv run python -m pytest -q
```

The suite starts its own PostgreSQL if `initdb` and `pg_ctl` are on `PATH`.
`initdb` refuses to run as root; in that case, point the tests at a running
server instead:

```sh
TEST_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/postgres \
  uv run python -m pytest -q
```

Use `python -m pytest`, not bare `pytest`, so that `scripts/` is importable.
moto mocks S3 and SQS, so no AWS account is needed. The worker tests need no
database: `python -m pytest worker/tests` runs anywhere. Set `ECC_TEST_ENGINE`
to test a prebuilt engine, such as a Metal build. `scripts/check_deploy.sh`
checks everything under `deploy/` and needs kubectl, kubeconform,
`docker compose`, systemd-analyze and shellcheck.

The [local stack](docs/local-stack.md) runs the whole platform locally: site,
verifier, emulated AWS and a CPU worker uploading real points. It is the
quickest way to try a gateway or worker change end to end.

### Pull requests

- Keep each PR to one change. Explain what it does and how you checked it.
- Add or update a test for any behavior change. CI runs the full suite,
  `cfn-lint` on `infra/`, and builds the web, admin, audit, worker and worker-cpu images.
- Anything that touches verification, collision handling or admission needs a
  test that shows the bad case being rejected, not just the good case passing.
- Don't claim a speedup or a result without the measurement or receipt behind
  it. Put receipts under `verification/` or link them from the PR.
- No secrets, keys or account IDs in commits, issues or PR text.

### Good places to start

- The [open problems](problems/README.md), especially verifier throughput and
  sustained ingest capacity.
- Worker kernel performance for the binary and prime field walks.
- An independent reimplementation of the point and collision checks. A second
  verifier written from the spec is the best way to find bugs in the first.
- Website and docs fixes. Templates are in `src/ecc_challenges/templates/`;
  the CSP allows inline styles only, no scripts or external assets.

## Reporting a security problem

If you find a way to forge accepted work, read other contributors' data or
get around key revocation, do not open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/isogeny-labs/ecc-challenges/security/advisories/new)
instead.

## Progress metrics (daily lab report)

`tools/progress_metrics.py` exports this repository's progress metrics as JSONL
for the lab-wide daily report (`/Volumes/SSD990-2/progress-metrics`). It reads
the committed challenge-instance registry at `origin/main` (override with
`--ref` or `PM_REF`) and prints one sample per line: `metric, value, date,
problem, unit, better, kind, label, source`.

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
