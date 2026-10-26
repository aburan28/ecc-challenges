# Contributing

There are two ways to help: run a worker, or work on the code.

## Run a worker

Sign in at [ecc-challenges.com](https://ecc-challenges.com), create an API key
and run the command your dashboard shows.
[Running a worker](docs/running-a-worker.md) covers every other setup:
CPU-only, Compose, Kubernetes, systemd, Podman, Slurm and Modal. If something
looks wrong, such as rejected uploads, a crash or a number on the status page
that does not add up, [open an issue](https://github.com/aburan28/ecc-challenges/issues/new/choose)
with the worker log. Never paste your API key.

## Work on the code

The repository holds the website and worker API, the worker and its CPU,
CUDA and Metal engines, the verifier, the database migrations, the
infrastructure templates and the deployment recipes. The
[project overview](README.md) explains how the pieces fit together.

### Set up and run the tests

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/) and
PostgreSQL 16.

```sh
uv sync --extra test
uv run python -m pytest -q
```

The suite starts its own PostgreSQL when `initdb` and `pg_ctl` are on your
`PATH`. `initdb` refuses to run as root; in that case, point the tests at a
running server:

```sh
TEST_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/postgres \
  uv run python -m pytest -q
```

Run pytest through `python -m` as shown. S3 and SQS are mocked, so no AWS
account is needed. The worker tests need no database and run on any platform:

```sh
uv run python -m pytest worker/tests
```

Set `ECC_TEST_ENGINE` to test a prebuilt engine, such as a Metal build.

The [local stack](docs/local-stack.md) runs the whole platform on one machine:
site, verifier, emulated AWS and a CPU worker uploading real points. It is the
quickest way to try a gateway or worker change end to end.

### Pull requests

- Keep each PR to one change. Explain what it does and how you checked it.
- Add or update a test for any behavior change. CI runs the full suite, lints
  the infrastructure templates, checks the deployment recipes and builds every
  image.
- Anything that touches verification, collision handling or admission needs a
  test that shows the bad case being rejected, not just the good case passing.
- Don't claim a speedup or a result without the measurement behind it. Commit
  the receipt with the change or link it from the PR.
- No secrets, keys or account IDs in commits, issues or PR text.

### Good places to start

- The [open problems](problems/README.md), especially verifier throughput and
  sustained ingest capacity.
- Worker kernel performance for the binary and prime field walks.
- An independent reimplementation of the point and collision checks. A second
  verifier written from the spec is the best way to find bugs in the first.
- Website and documentation fixes. The site's content security policy allows
  inline styles only: no scripts or external assets.

## Reporting a security problem

If you find a way to forge accepted work, read other contributors' data or
get around key revocation, do not open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/aburan28/ecc-challenges/security/advisories/new)
instead.
