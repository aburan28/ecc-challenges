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
