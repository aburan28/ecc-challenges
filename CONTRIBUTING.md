# Contributing

There are two ways to help: run a worker, or work on the code.

## Run a worker

Sign in at [ecc-challenges.com](https://ecc-challenges.com), create an API key
and run the `docker run` command your dashboard shows. The
[README](README.md#contribute) has the details, and the
[Modal guide](docs/modal-workers.md) covers rented GPUs. If something looks
wrong (rejected uploads, a crash, a number on the status page that doesn't add
up), [open an issue](https://github.com/aburan28/ecc-challenges/issues/new/choose)
with the worker log. Never paste your API key.

## Work on the code

### Layout

| Path | What it is |
| --- | --- |
| `src/ecc_challenges/` | FastAPI website and worker API (`app.py`), templates, status page |
| `src/ecc_challenges/multicurve/` | v2 protocol for all 24 curves: codec, admission, verification, index |
| `worker/` | Contributor container: client, generic CPU/CUDA engine |
| `verifier/` | Replay and verification code used by the audit image |
| `sql/` | PostgreSQL migrations, applied in order |
| `infra/` | CloudFormation stacks |
| `scripts/` | Deploy, publish and live-check scripts (need AWS credentials) |
| `pool/` | Separate small-curve pool and STARK prototype, with its own CI |
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

Use `python -m pytest` rather than bare `pytest` so that `scripts/` is
importable. S3 and SQS are mocked with moto; no AWS account is needed.

To look at the site locally, build an app with a stub database the same way
`tests/test_catalog.py` does and serve it with uvicorn.

### Pull requests

- Keep each PR to one change. Explain what it does and how you checked it.
- Add or update a test for any behaviour change. CI runs the full suite,
  `cfn-lint` on `infra/`, and builds all four images.
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
  verifier written from the spec is the best way to catch bugs in the first.
- Website and docs fixes. Templates are in `src/ecc_challenges/templates/`;
  the CSP allows inline styles only, no scripts or external assets.

## Reporting a security problem

If you find a way to forge accepted work, read other contributors' data or
get around key revocation, please don't open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/aburan28/ecc-challenges/security/advisories/new)
instead.
