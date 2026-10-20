# Offline checks of the public catalog image

On 2026-10-20 UTC, the live `/v1/campaigns` catalog advertised the combined
worker image `public.ecr.aws/o1a3z1g6/ecc-challenges-worker@sha256:df0a46d4c5b819c5ef80bce8eef7edd4164c6c9e99d74f3f4eda45c6234531b3`.
This digest differs from the image in the [earlier sandbox checks](../sandbox-runtime-20261019/README.md),
so both Docker runtimes were checked again with the exact catalog digest.

| Runtime | Host | Complete receipt SHA-256 | Private host archive |
| --- | --- | --- | --- |
| Kata runtime-rs QEMU 4.2.0 | Temporary EC2 `i-0a1738663fd8dea73`, `m7i-flex.large`, nested KVM, Ubuntu 24.04, kernel `7.0.0-1014-aws`, Docker 29.1.3 | `b5b8bfc90cf5d6e8d8f8b899a2e2ec13b81ea347050b4dc5007ca746083ad5cf` | `s3://ecc-challenges/validation/kata-current-20261020-004946-116813ab/result.tar.gz` (SHA-256 `68d6dab80e69f2a82cbf3329e46975bbb30b7b1eeb5cf55609634db7046d8ddf`) |
| gVisor `runsc` release-20261012.0 | Temporary EC2 `i-02a6540ed31ef4a3a`, `m7i-flex.large`, Ubuntu 24.04, kernel `7.0.0-1014-aws`, Docker 29.1.3 | `fc1c8ddc28d20f7f24924053e0727d7b163c5598ba79280d4282fe55ee33439a` | `s3://ecc-challenges/validation/gvisor-current-20261020-005717-438a172d/result.tar.gz` (SHA-256 `628554dc125cd0e5a8d0badac23b1e772039b54ffd890a9b75dd9844e772c791`) |

Both hosts checked out source commit `5de930402849be96ed0e68fbd2f54e7fc3cd5983`
and ran `scripts/check_sandbox_runtime.py --all-curves` against the pinned
image. Each run exited 0, passed the CPU engine self-test and probe, wrote and
resumed a mode-0600 checkpoint as UID 10001, and completed 168 CPU checks
across 24 campaigns and 72 cases. The two detailed receipts match exactly;
their 72 rows also match the earlier image's rows. The hosts used no gateway
key, had no inbound security group rules or IAM instance profile, and were
terminated after the private archives were retrieved.

The [GitHub Actions dispatch](https://github.com/aburan28/ecc-challenges/actions/runs/39349775179)
for this ECR digest failed during image pull with ECR Public
`toomanyrequests: Data limit exceeded`, before the gVisor runtime ran. The
successful gVisor result above came from the temporary host after explicit
`runsc install` and Docker restart. These receipts establish offline CPU
execution and checkpoint persistence for the exact image digest, not a live
gateway contribution, Kubernetes RuntimeClass deployment, or sandboxed GPU
execution.
