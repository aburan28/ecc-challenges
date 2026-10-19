# Kata Containers and gVisor worker checks

The Linux worker image is an OCI container, but the project has no successful
worker run under Kata Containers or gVisor yet. These recipes are opt-in
validation paths. They do not change public campaign enrollment. Check the
[live catalog](https://ecc-challenges.com/v1/campaigns) before starting a real
worker; protocol 2 remains gated while `multicurve_enabled` is false.

## Offline CPU smoke

The `Sandbox worker runtimes` workflow runs the gVisor smoke on an Ubuntu
GitHub runner whenever this check or its overlays change. The pull-request
job uses the pinned image advertised by the public campaign catalog when
24-curve enrollment was enabled and requires the complete offline 24-curve
check. After a later image release, dispatch the workflow on the default
branch with the new exact digest and `all_curves=true`. Keep the resulting
version log and detailed receipt.

To run the Kata job, enroll a dedicated Linux x64 runner with the `kata-kvm`
label, `/dev/kvm`, Docker 26 or newer, and the `kata` Docker runtime registered
with the Kata runtime-rs QEMU configuration. Then dispatch the workflow with
`validate_kata=true` and the same image digest. The job fails at preflight if
KVM is unavailable. The [Kata installation guide](https://github.com/kata-containers/kata-containers/blob/main/docs/installation.md)
documents the Docker registration and virtualization requirements. No such
runner is currently enrolled, so a queued Kata job is not a validation result.

Use a Linux host with Docker and a configured `runsc` or Kata runtime. Supply
the exact CPU or combined worker image digest you intend to run:

```sh
python3 scripts/check_sandbox_runtime.py \
  --runtime runsc \
  --all-curves \
  --receipt runsc-worker-check.json \
  --image 'ghcr.io/aburan28/ecc-challenges-worker-cpu@sha256:YOUR_DIGEST'
# On a host with a Docker Kata runtime, replace runsc with its registered name.
```

The check requires a full SHA-256 image digest and verifies that the requested
runtime is registered. It runs the engine self-test and CPU probe, then writes
and resumes a mode-0600 checkpoint from two separate containers using UID
10001. With `--all-curves`, it also requires the image's offline `--check`
entry point to complete at least 168 checks across all 24 campaigns. Use an
image release that contains that entry point; omit the flag only when checking
an older image's runtime mechanics. Both containers use the same disposable
named volume. The check uses no
API key or network access and removes its volume when done. The required
`--receipt` path keeps the detailed 24-curve control receipt outside that
volume as a new mode-0600 file. Retain it with the Docker/runtime versions,
host architecture, and exit code as the evidence for a runtime-specific
result. A pass establishes only CPU
arithmetic and local persistence in that configured runtime.

## Kubernetes CPU examples

The `deploy/kubernetes/gvisor-cpu` and `deploy/kubernetes/kata-cpu` overlays
add `runtimeClassName: gvisor` or `runtimeClassName: kata` to the existing
CPU StatefulSet. The cluster administrator must install and test the matching
[RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/);
the names are examples and can be changed in the overlay. Replace the all-zero
image digest in the selected overlay with the reviewed CPU image digest before
applying it; the placeholder deliberately cannot be pulled. The existing per-replica
volume, unprivileged UID, and 120-second shutdown grace period still apply.

After the offline smoke passes, use a private key and a canary-enabled gateway
to verify one real contributor registration, upload, audit, index, restart and
closure. Preserve the state volume and server receipts. Those steps are
required before describing either runtime as supported for live campaigns.

## GPU boundary

The CPU overlays do not request GPUs. gVisor's
[NVIDIA guide](https://gvisor.dev/docs/user_guide/gpu/) requires `nvproxy` and
a compatible host driver for CUDA. Kata's
[NVIDIA GPU guide](https://github.com/kata-containers/kata-containers/blob/main/docs/use-cases/NVIDIA-GPU-passthrough-and-Kata-QEMU.md)
uses GPU passthrough and a GPU-specific runtime class and device resource;
adding `runtimeClassName` to the existing `nvidia.com/gpu` overlay is not
sufficient. Test the actual GPU engine, exact curve outputs, restart, upload
and drain on each configured stack before claiming GPU support.
