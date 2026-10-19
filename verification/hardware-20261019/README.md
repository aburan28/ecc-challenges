# All-curve worker checks, 2026-10-19

The current worker arithmetic passed finite correctness checks for every one of
the 24 pinned Certicom instances. These results check exact outputs and rejected
inputs. They do not measure collection throughput, certify work counts or solve
an open challenge.

| Execution | Curves | Checks | Result |
| --- | ---: | ---: | --- |
| Final CPU-only amd64 Docker image, CPU | 24 | 168 | Passed |
| Final combined amd64 Docker image, CPU | 24 | 168 | Passed |
| Current source on RunPod RTX 4090, CPU and CUDA | 24 | 336 | Passed |

The two Docker checks used the actual `--check` entry point, default UID/GID
10001, two CPU threads, no contributor credential, and `--network none`.
Their local image IDs are in `cpu-amd64/image.json` and
`combined-amd64/image.json`. Each directory retains its summary, engine hashes,
and compressed full inputs and outputs in `controls.tgz`.

The RunPod job used the pinned CUDA 13.0.2 development image from the worker
Dockerfile's build stage and compiled the current CPU and universal CUDA engines
with the same flags as the Dockerfiles. It ran on an RTX 4090 with driver
595.91.07. `runpod/source.tgz` and the file hashes in `runpod/launch.json` bind
the exact uploaded source. `runpod/results.tgz` contains every generated input,
native output and log; `runpod/receipt.json` records all 24 completed campaigns
and both engine hashes. This is a current-source GPU check; the development
container is distinct from a published final worker image.

The requested cheaper RTX 4000 Ada allocation lost capacity before deployment;
the 4090 allocation was used instead. A 30-minute automatic termination bound
was set. The local controller was interrupted after starting the job; a recovery
controller fetched the completed artifacts and terminated the same pod. The
provider inventory confirmed its removal, as recorded in `runpod/launch.json`.

The focused source suite completed with **83 passed and 26 skipped**:

```sh
uv run --no-sync python -m pytest -q -o faulthandler_timeout=90 \
  worker/tests tests/test_native_curves.py tests/test_generic_client.py tests/test_catalog.py
```

The skips concern unavailable platform backends or toolchains. The initial
sandboxed invocation stopped responding in the HTTP test client; the successful
run used the approved executor networking. Both workflow YAML files parsed and
`git diff --check` passed. Native ARM64 Docker checks are configured in CI;
they are separate from these amd64 local receipts. No FPGA backend was added.

## Published release

Both images are public GitHub Container Registry packages linked to
`aburan28/ecc-challenges`. The dashboard and deployment recipes use:

- `ghcr.io/aburan28/ecc-challenges-worker:latest` for CPU/CUDA on Linux amd64.
- `ghcr.io/aburan28/ecc-challenges-worker-cpu:latest` for CPU on Linux amd64 and arm64.

The dashboard pins immutable digests. [The GHCR publication receipt](ghcr-publication.json)
records anonymous manifest access, exact digest preservation from the validated
images, both CPU architectures and the publishing workflow runs that confirmed
public visibility and repository association. The `:gpu` and `:cpu` aliases in
the combined package also select these respective releases. The temporary
isolated publishing runners completed their single jobs and were removed.

The original ECR Public copies remain available. Their immutable digests and
anonymously verified manifests are recorded in [published-images.json](published-images.json).

| Additional execution | Curves | Checks | Result |
| --- | ---: | ---: | --- |
| Native ARM64 final CPU image on AWS CodeBuild | 24 | 168 | Passed |
| Published combined image on RunPod RTX 4090, CPU and CUDA | 24 | 336 | Passed |

[Native ARM64 evidence](cpu-arm64/image.json) binds the public child manifest to
its source and native `aarch64` build. Its compressed inputs and receipt are
retained alongside it. The hosted GitHub native ARM job also passed before its
superseded publication run was cancelled.

The first final-image GPU allocation failed before container startup because
its inherited base required CUDA 13.3. The release corrects the startup metadata
to CUDA 13.0 for the statically linked all-curve engine and its native SASS,
using NVIDIA's CUDA 13.x minor-version compatibility. The driver check remains
enabled. The corrected exact published image then passed on RunPod driver
580.65.06 as UID 10001. [The receipt](runpod-published/receipt.json), bootstrap,
all generated controls and device information are retained. Every validation
pod was terminated; [the inventory check](runpod-cleanup.json) confirms this.
Existing contributor pods were preserved.

## Live contribution checks

[The live matrix](live-cpu/summary.json) passed on all 24 curves using the exact
published CPU image, four containers at a time. Each used the current production
namespace, registered a real run, advanced native walk states, independently
checked four checkpoint witnesses with `R=aG+bQ` and the run/trail-derived `b`,
resumed the same checkpoint, drained any emitted records, and closed the run.
The first/restart checkpoints, server statuses and closure are retained per
curve. ECC2-79 emitted two production-predicate records and ECCp-79 emitted one;
all three reached the live index. The other short checks emitted no records;
their sparse production DP predicates were preserved. No production-point or
throughput result is claimed for those curves.

The initial matrix overlapped the private allowlist rolling update and failed
on mixed old/new admission settings. It is retained in
`rolling-canary-attempt.tgz`. Three runs were created by that attempt; all were
closed after the update, as recorded in `rolling-attempt-closures.json`. The
successful matrix waited for the deployment to stabilize first.

The final source suite passed **145 tests, with 26 platform/toolchain skips**
against a disposable local PostgreSQL instance; [the output](tests.txt) is
retained. Added integration checks exercised retryable v2 backlog 503 recovery
and an injected SQS send outage followed by repair and idempotent indexing.
These fault checks used local PostgreSQL and mocked AWS, not production fault
injection. This release does not establish a larger fleet's capacity, every
curve's collection rate, or billed cloud cost. Existing admission bounds and
autoscaling remain in force. FPGA acceleration is deferred.

Public enrollment is enabled through a parameter-only ECS rollout, with the
private canary allowlist cleared. See `public-enablement.json` and the final
public gateway snapshot for observed rollout status. No ongoing validation
workers are left running.

## Challenge-page layout and GHCR rollout

The published parameter table wraps long canonical values and labels each
point's x and y coordinates on separate lines. All 24 live HTML pages were
rendered at widths of 320, 390, 768, 1080 and 1440 pixels: 120 cases, with no
horizontal table scroll or overflowing cells. Every displayed parameter value
matched the pinned registry render. See [the layout receipt](parameter-layout.json).

The web-only rollout also switches the dashboard's pinned CPU and GPU images
to the public GHCR packages. [The rollout receipt](ghcr-layout-rollout.json)
records the deployed web digest, stable services and preservation of every
other live stack parameter.
