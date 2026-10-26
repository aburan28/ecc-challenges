# ECC Challenges

A public, distributed Pollard rho search on the
[Certicom ECC challenges](https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf):
24 elliptic-curve discrete logarithm problems from 79 to 359 bits. Ten were
solved between 1997 and 2004; fourteen remain open. Workers walk a curve and
send back distinguished points (DPs), the walk positions that satisfy a
sparse predicate. The server checks every point and looks for a collision
between any two walks.

## Take part in three steps

1. Sign in with GitHub at [ecc-challenges.com](https://ecc-challenges.com)
   and create an API key. The key is shown once; the installers below save it
   for you, or keep it in `~/.config/ecc-challenges/api-key` with mode `0600`.
2. Start one worker on the machine you have:

   | Machine | Command |
   | --- | --- |
   | Linux with Docker, CPU or NVIDIA GPU | the `docker run` command on your dashboard |
   | Linux without Docker, or a Mac (Metal) | `curl -fsSL https://ecc-challenges.com/install.sh \| sh` |
   | Windows, in PowerShell | `irm https://ecc-challenges.com/install.ps1 \| iex` |

   Choose the challenge with `ECC_CAMPAIGN`; the installers default to
   `eccp-131`. Keep the worker's data directory between restarts. It holds the
   run identity and checkpoint.
3. Check that it reports. Within a few minutes the run appears on your
   dashboard with its heartbeat, uploads and verification state, and in the
   active count on the [status page](https://ecc-challenges.com/status).

[Running a worker](docs/running-a-worker.md) has the full recipes: Docker,
Compose, Kubernetes, systemd, Podman, Slurm/Apptainer, Modal, macOS and
Windows, on GPU (CUDA, Metal) or CPU (x86-64 and arm64). A coding agent can do
the setup: [agent setup](docs/agents.md) has a prompt and the reusable
[worker skill](https://ecc-challenges.com/skill.md). If something goes wrong,
see [help](https://ecc-challenges.com/help).

To work on the code, read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[open problems](problems/README.md). The [local stack](docs/local-stack.md)
runs the whole platform on one machine. Live numbers are on the
[status page](https://ecc-challenges.com/status) and at
[`/v1/status`](https://ecc-challenges.com/v1/status).

## How the gateway is built

`ecc-challenges.com` is the contribution gateway for the 24 published Certicom
ECC challenges. A contributor signs in with GitHub, creates a revocable API
key, and runs a pinned Linux container on a workstation or cloud provider.
The generic v2 worker handles prime and binary fields up to 359 bits. Its
CPU, NVIDIA CUDA and Apple Metal backends share one arithmetic core
(`worker/generic/ec_core.h`) and run on Linux, macOS and Windows. Existing
`ecc2k-130` v1 state keeps its original Frobenius walk and 32-byte record
format.

### Release status

All 24 curves were enabled on the public gateway on 19 October 2026.
The final CPU image passed all-curve checks natively on amd64 and arm64. The
final combined CPU/CUDA image passed 336 checks on a RunPod RTX 4090. Bounded
live CPU checks passed registration, independently checked checkpoint
witnesses, same-run restart and closure on every curve. Short runs emitted
production points on ECC2-79 and ECCp-79. These checks do not establish point
rates on larger curves or fleet capacity. All validation pods were terminated.
See the
[release receipts](verification/hardware-20261019/README.md) and
[earlier correctness controls](verification/all-curves-20261016/README.md).

Two public worker images are built from this repository:

| Image | Hardware |
| --- | --- |
| `ecc-challenges-worker` | Linux amd64 CPU or NVIDIA CUDA |
| `ecc-challenges-worker-cpu` | Linux amd64 and arm64 CPU |

Each image supports all 24 curves. Always run the immutable digest shown on
your dashboard or in [`/v1/campaigns`](https://ecc-challenges.com/v1/campaigns);
the gateway publishes digests only, never a moving tag. FPGA acceleration is deferred.

A v2 worker uploads immutable, checksum-bound batches directly to versioned S3.
The server verifies every point relation and conditionally indexes exact
points in DynamoDB.
PostgreSQL holds identity, quotas, admission cursors and processing metadata.
See the [all-curve storage design](docs/multicurve-storage.md) for namespace
binding, sizing, collision safety, compaction and archive reconstruction.

```mermaid
flowchart LR
  GPU[Worker: CPU or GPU container] -->|API key, small control JSON| ALB[WAF + HTTPS ALB]
  ALB --> API[Stateless API tasks]
  GPU -->|checksum-bound direct PUT| S3[Versioned immutable S3 archive]
  API -->|namespace and admission receipts| S3
  API -->|identity, quotas, cursor, outbox| PG[(Private PostgreSQL)]
  PG -->|durable delivery and repair| SQS[Audit queue]
  SQS --> Audit[Full v2 point verification]
  S3 -->|pinned raw version| Audit
  Audit --> Index[Conditional DynamoDB point index]
  Index --> Resolver[Coefficient collision + scalar check]
  Resolver -->|immutable result| S3
```

The diagram shows v2. The separate legacy `ecc2k-130` protocol keeps its
sampled trail replay and PostgreSQL candidate projection. Historical points
are imported one way by `scripts/historical_import.py` (see
[operations](docs/operations.md#historical-point-import)). New generic points
have their own curve and walk namespaces.

## Running GPU workers

Run one container per GPU. Set `ECC_GPU` to the device index (the dashboard
command takes it from a `GPU_ID` shell variable) and give each GPU its own
persistent data directory. Never restart a run with a fresh directory while
its earlier seed range is active. On Linux, install the NVIDIA Container
Toolkit so Docker's `--gpus all` can expose the card. For an RTX PRO 6000
Blackwell worker in your own Modal account, follow the
[Modal worker guide](docs/modal-workers.md). Start with one bounded worker and
check its registration, upload, replay and index status before adding more.

The container pauses collection when the local upload spool reaches its limit
and retries with the same run ID. A server `429` or `503` means back off; do
not discard the local file. Revoking the key stops uploads for its runs. A key
is not an AWS credential. It grants no bucket listing, archive reads or
database access, and a signed upload capability permits only its one bounded
object write.

## What an accepted upload means

For v2, admission checks key and account limits, UUID ownership, namespace,
cursor, record alignment and checksum-bound S3 object metadata. Its
acknowledgment means the versioned raw bytes and the admission receipt are
durable. The audit worker then checks every record's DP predicate, curve
equation and `R=aG+bQ` relation before any DynamoDB insertion. A collision
rechecks its source receipts and coefficient witnesses, computes the candidate
scalar and checks `dG=Q`. Verified coefficient relations do not attest
reported trail lengths, so the site's work and completion figures are
estimates from point counts ([docs/campaigns.md](docs/campaigns.md)), not
verified work.

The separate legacy `ecc2k-130` sigma protocol accepts DP30, DP32 and DP34
(the number is the DP threshold in bits). The NVIDIA dashboard recommends DP34
for new runs; existing runs keep their threshold. See
[threshold selection](docs/running-a-worker.md#ecc2k-130-legacy-sigma-thresholds).

The API checks framing, run ID, canonical orbit-key shape at the run's
threshold, body size, cursor, key status, key rate and quota, and global
pending capacity. It stores the exact bytes under a checksum-bound, write-once
S3 key. A successful upload acknowledges archival and the committed cursor. It
does not assert that each record came from the claimed seed.

The first batch of each run is selected for independent replay. Later batches
are selected after sealing with probability 1/128. A passing first replay
opens indexing for that run; sampled and unselected states stay distinct.
All public points are kept in `ecc_public.candidate_points`, separate from the
existing campaign's authoritative table. A failed later replay suspends the
run, revokes its key, disqualifies already indexed candidate points and
quarantines its leads. A collision lead triggers exact replay of both trails
and a pinned host solver. A separate golden-model multiplication must also
check the reported scalar against the challenge points before the lead is
marked verified.

These checks contain abuse and preserve evidence, but sparse random sampling
cannot certify every point in a batch. The service still relies on quotas,
queue backpressure, operator monitoring and exact replay of every collision
lead.

## Development

```sh
uv sync --extra test
# Needs PostgreSQL 16 binaries on PATH (initdb, pg_ctl), or set TEST_POSTGRES_DSN.
# macOS: PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
uv run python -m pytest -q
aws cloudformation validate-template --template-body file://infra/foundation.yaml
aws cloudformation validate-template --template-body file://infra/data.yaml
aws cloudformation validate-template --template-body file://infra/service.yaml
```

The integration suite starts isolated PostgreSQL and mocked S3 and SQS. It
checks key lifecycle, run assignment, upload idempotency, archival, outbox
repair, indexing, failed-replay quarantine and the HTTP worker protocol. The audit
image builds and runs the pinned arithmetic test before copying the replay
binary; its host solver is separately built from a pinned `crypto` source
commit. Source bundles and hashes are under `vendor/`.

## Deployment and capacity

[Operations](docs/operations.md) covers image publishing, AWS stacks,
Squarespace DNS, GitHub OAuth setup, rollout checks and queue alarms.
[Admission and capacity](docs/admission-and-capacity.md) covers the threat
model, rate limits and the measurable throughput gate. The capacity script
takes observed rates:

```sh
python3 scripts/capacity.py --gpus 1000 --steps-per-gpu-second 1000000000 \
  --steps-per-dp 350000000 --index-rows-per-task-second 20000 \
  --audit-seconds-per-record 600 --new-runs-per-hour 100
```

The numbers in that command are illustrative input, not campaign
measurements. Opening the site to a large fleet requires a measured replay
time and a sustained arrival and drain test. The
[capacity problem record](problems/dp32-verifier-and-ingest-capacity.md)
tracks both.

## Source and campaign boundary

The worker GPU base is the immutable image
`ghcr.io/aburan28/ecc2k130-worker@sha256:25299db686c6b73bb59edc1b5f6591d917ada4e5d933172a4eca7274a71e9421`.
The replay source is `aburan28/cryptanalysis` commit
`c8cc56d8c98705c7f84a21aae1e042453e0105e6`; the scalar resolver source
is `aburan28/crypto` commit
`79006eb85a400474e83f6a76fac0dca6b60a8b5d`.
The isolated S3 seed registry is the admission authority for public run IDs.
The original campaign table stays in its own account, and this service never
writes to it. Until its points are imported and backmatched, collision lookup
covers new public candidates only. The paper's quantitative claims go through
the paper's separate evidence and submission process.
