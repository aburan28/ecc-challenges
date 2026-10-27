# Kata Containers and gVisor worker checks

The Linux worker image passed the offline CPU smoke under both gVisor and
Kata Containers with the pinned published digest. The detailed receipts and
host facts are in [`verification/sandbox-runtime-20261019`](../verification/sandbox-runtime-20261019/README.md).
The later image advertised by the public catalog on 20 October 2026 has its own
[Kata and gVisor receipts](../verification/sandbox-runtime-20261020/README.md).
These recipes do not change public campaign enrollment. Check the
[live catalog](https://ecc-challenges.com/v1/campaigns) before starting a
worker that uploads.

## Offline CPU smoke

The `Sandbox worker runtimes` workflow runs the gVisor smoke on an Ubuntu
GitHub runner whenever this check or its overlays change. The pull-request
job uses the pinned image advertised by the public campaign catalog when
24-curve enrollment was enabled and requires the complete offline 24-curve
check. After a later image release, dispatch the workflow on the default
branch with the new exact digest and `all_curves=true`. Keep the version log
and the detailed receipt.

To rerun the Kata job in GitHub Actions, enroll a dedicated Linux x64 runner with the
`kata-kvm` label, `/dev/kvm`, Docker 26 or newer, and the `kata` Docker runtime registered
with the Kata runtime-rs QEMU configuration. Then dispatch the workflow with
`validate_kata=true` and the same image digest. The job fails at preflight if
KVM is unavailable. The [Kata installation guide](https://github.com/kata-containers/kata-containers/blob/main/docs/installation.md)
documents the Docker registration and virtualization requirements. No such
runner is currently enrolled. The separate EC2 KVM smoke recorded in the
verification directory does not establish that the GitHub Actions Kata job
or a Kubernetes deployment works.

Use a Linux host with Docker and a configured `runsc` or Kata runtime, and
supply the exact CPU or combined worker image digest you intend to run:

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
and resumes a mode 0600 checkpoint from two separate containers running as UID
10001. With `--all-curves`, it also requires the image's offline `--check`
entry point to complete at least 168 checks across all 24 campaigns. Use an
image release that contains that entry point; omit the flag only to check an
older image's runtime mechanics. Both containers use the same disposable named
volume. The check uses no API key or network access and removes its volume when
done. The required `--receipt` path keeps the detailed 24-curve receipt outside
that volume, as a new mode 0600 file. Keep it with the Docker and runtime
versions, host architecture and exit code as the evidence for a
runtime-specific result. A pass establishes only CPU arithmetic and local
persistence in that runtime.

## Kubernetes CPU examples

The `deploy/kubernetes/gvisor-cpu` and `deploy/kubernetes/kata-cpu` overlays
add `runtimeClassName: gvisor` or `runtimeClassName: kata` to the existing
CPU StatefulSet. The cluster administrator must install and test the matching
[RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/);
the names are examples and can be changed in the overlay. Before applying an
overlay, replace its all-zero image digest with the reviewed CPU image digest;
the placeholder deliberately cannot be pulled. The per-replica volume,
unprivileged UID and 120-second shutdown grace period still apply.

After the offline smoke passes, use a private key and a canary-enabled gateway
to verify one real registration, upload, audit, index, restart and closure.
Keep the state volume and server receipts. Both runtimes need these steps
before they can be called supported for live campaigns.

## GPU boundary

The CPU overlays do not request GPUs. gVisor's
[NVIDIA guide](https://gvisor.dev/docs/user_guide/gpu/) requires `nvproxy` and
a compatible host driver for CUDA. Kata's
[NVIDIA GPU guide](https://github.com/kata-containers/kata-containers/blob/main/docs/use-cases/NVIDIA-GPU-passthrough-and-Kata-QEMU.md)
uses GPU passthrough and a GPU-specific runtime class and device resource;
adding `runtimeClassName` to the existing `nvidia.com/gpu` overlay is not
sufficient. Test the actual GPU engine, exact curve outputs, restart, upload
and drain on each configured stack before claiming GPU support.
