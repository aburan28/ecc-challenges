# Offline sandbox worker validation

Both checks used the published Linux worker image
`ghcr.io/aburan28/ecc-challenges-worker@sha256:c7b58f784a8d8ea62d1649062387e76558d627c166fef14f185b6dab417a6f2e`.
The checks used `scripts/check_sandbox_runtime.py --all-curves` with no gateway
key or campaign submission. The JSON files in this directory are the complete
receipts, including 72 detailed rows each. Their detailed all-curve receipts
match exactly, including the 24 campaign identities, inputs, states, and
distinguished-point hashes.

| Runtime | Host and provenance | Result | Receipt SHA-256 |
| --- | --- | --- | --- |
| gVisor `runsc` | GitHub Actions Ubuntu 24.04, Docker 28.0.4, `runsc` release-20261012.0, source `2cd3ddb4a2aae01c0a10c89afd47d19edf6c4239`; [successful run and host artifact](https://github.com/aburan28/ecc-challenges/actions/runs/39323962252/artifacts/12226215708) | CPU engine self-test and probe; checkpoint cursors 1 and 2 as UID 10001, mode 0600; 24 campaigns, 168 checks, 72 cases | `3d62d4eea7fbae42d9b8772b73faddf1563dffa24e62dad355bf9e194cc6fb0f` |
| Kata runtime-rs QEMU 4.2.0 | EC2 `m7i-flex.large` with nested virtualization and `/dev/kvm`, Ubuntu 24.04, kernel `7.0.0-1014-aws`, Docker 29.1.3, source `4e7fa51e7e73f633c99111ae0abc67337fd0acec` | Same CPU engine, checkpoint, and all-curve checks; exit 0 | `dc2e7aac26603ac1e8609586b8e902d2f295db0bc7b43d0f3c3503821142612c` |

Kata was run on 2026-10-20 UTC on temporary instance `i-0c2f37f86b547692d`.
The instance had no inbound security group rules or IAM instance profile. Kata's
static `amd64` 4.2.0 archive was checked against SHA-256
`b828904fa3f1e49ddd7dc799c72cb1503cd1e772d354c3987c8d4189b2a623a8`
before extraction. The exact host log, runtime details, receipt and exit code
are retained in the private bucket at
`s3://ecc-challenges/validation/kata-20261020-003218-caeafd18/result.tar.gz`
(archive SHA-256 `db4e6d1ab85152a9ceaa183e1d8b845800515d564d5f43a4913a2a59755fc12e`).
The instance was terminated and its temporary security group deleted after the
receipt was retrieved.

These checks establish offline CPU execution and local checkpoint persistence
under the named Docker runtimes for this image digest. They do not establish
Kubernetes RuntimeClass integration, a live gateway contribution, or GPU
execution under either sandbox.
