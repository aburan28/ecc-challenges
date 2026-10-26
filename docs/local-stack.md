# Local stack

`deploy/local/compose.yaml` runs the whole platform on one machine: the
website and worker API, the verification and repair workers, PostgreSQL,
emulated S3/SQS/DynamoDB (LocalStack), and optionally a CPU worker. Use it to
work on the gateway, the verifier or the worker without an AWS account.

```sh
docker compose -f deploy/local/compose.yaml up -d --build
docker compose -f deploy/local/compose.yaml run --rm -T init \
  python scripts/local_stack.py key me > .local-key
chmod 644 .local-key          # the worker container runs as UID 10001
docker compose -f deploy/local/compose.yaml --profile worker up -d --build worker
docker compose -f deploy/local/compose.yaml logs -f worker audit
```

Open <https://localhost:8443> and accept the local certificate. The status page
shows points arriving, then verified. The worker runs `eccp-79` unless you set
`ECC_CAMPAIGN`, and a CPU worker finds a point every few seconds on the small
curves. To switch curves, stop the worker, remove its state
(`docker volume rm ecc-local_worker-data`) and start it again with
`ECC_CAMPAIGN=ecc2-131` (or any catalog ID) set in your shell.

Stop with `docker compose -f deploy/local/compose.yaml down`. Add `-v` to wipe the database, archive and worker state.

## How it fits together

| Service | Role |
| --- | --- |
| `proxy` | Caddy with its own local CA. Serves the site as `ecc-challenges.test` and `localhost:8443`, and S3 as `s3.us-west-2.amazonaws.com` |
| `localstack` | S3, SQS and DynamoDB |
| `postgres` | Accounts, keys, runs, batches |
| `init` | Applies `sql/` (every start; migrations are idempotent), creates the bucket, queues and point table, publishes the CA certificate |
| `web` | Website and worker API, with all 24 curves enabled |
| `audit` | Verifies every uploaded point and queues it for the point index |
| `reconcile` | Delivers queued work, repairs anything left half-done and merges the point index every 30 seconds |
| `worker` | The CPU worker image, pointed at the local gateway |

The worker talks only to an HTTPS gateway and uploads only to
`*.amazonaws.com`, the same restrictions it has in production. The stack meets
them with the TLS proxy and network aliases instead of relaxing the worker.
Those names exist only on the stack's Docker network.

The stack uses LocalStack instead of moto because the gateway checks two things
moto does not provide: the SHA-256 checksum S3 records for a signed upload, and
DynamoDB's `TableId`.

The stack uses the compact point index (`POINT_INDEX_MODE=compact`, see
[multicurve storage](multicurve-storage.md)). Set `POINT_INDEX_MODE=dynamodb` in your shell
before `up` to use the DynamoDB index instead. To check collision handling end
to end, let a worker upload a few points, then inject a known-answer collision:

```sh
docker compose -f deploy/local/compose.yaml run --rm -T \
  -v "$PWD/.local-key:/app/.local-key:ro" init \
  python scripts/local_collision_check.py eccp-79 /app/.local-key
```

It registers a second run, uploads one of the worker's points written with
that run's own coefficients, and passes once the resolver recovers the curve's
published reference scalar.

GitHub sign-in is not configured locally, so the dashboard is unusable;
`scripts/local_stack.py key` creates keys instead. It refuses to run unless
`LOCAL_STACK=1`, which only this compose file sets.
