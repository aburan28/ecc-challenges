# Unified worker release checks, 2026-10-17

This directory binds the release image, hosted checks, live contribution, and a bounded fresh-point load to retained receipts. The public gateway still reports `multicurve_enabled: false`; the capacity result below applies to ECCp-79 and a proposed one-L4 operating envelope only.

## Exact release

- Source commit: `5bd0a507869492de86a0d7a4f1bc569a0db0b417` (merged PR #22). Its four CI jobs passed on the merge commit.
- AWS CodeBuild: `ecc-challenges-release:9e84f422-7ba8-46ad-b89c-ef10d37cde93`, succeeded. The [release manifest](release-manifest.json) is S3 object `release-builds/5bd0a507869492de86a0d7a4f1bc569a0db0b417/manifest.json` in `ecc-challenges-data-104186811093`, version `IiihsgAi9YF6miBWuCasDzPhD10RL1U2`.
- Unified worker: `public.ecr.aws/o1a3z1g6/ecc-challenges-worker@sha256:d2a7354ee00f1bdddc0931895b19e1b60140812a1d315d223b897f28157a1f9f`. GitHub workflow run `39080671840` copied this exact manifest to `ghcr.io/aburan28/ecc-challenges-worker@sha256:d2a7354ee00f1bdddc0931895b19e1b60140812a1d315d223b897f28157a1f9f`; an unauthenticated manifest GET independently hashed to the same digest.
- The web and audit image digests in the manifest were deployed together with that worker digest. The [post-cleanup deployment snapshot](deployment-state.json) confirms five stable ECS services, the live GHCR digest, one retained private canary key, and public v2 enrollment still gated. Subsequent main commits through `8de59139b7ae0a50bd97e4843336b3532a3265b0` changed no worker, multicurve, app, or settings source files.

## Hosted correctness and contribution

The [L4 image receipt](l4-image-check.json) passed 168/168 frozen controls across all 24 campaigns as UID/GID 10001 using the CUDA engine. The [CPU image receipt](cpu-image-check.json) passed the same 168/168 controls across all 24 campaigns as UID/GID 10001 using the OpenMP engine. The controls exercise exact outputs and rejected malformed cases; they are not a live throughput measurement for every campaign.

The first [GPU contributor attempt](gpu-contributor-attempt-1.json) and its [diagnosed retry](gpu-contributor-attempt-2.json) exited before registration: the then-deployed gateway used a different DP setting and namespace from the new worker. Their new volumes contained only lock files. After deploying source-matched web and audit images, the [third attempt](gpu-contributor-attempt-3.json) passed on an L4: the worker uploaded four production-predicate records, the verifier indexed all four, a restart advanced the same run checkpoint, the external Modal volume matched the final checkpoint, and the run was closed. The failed receipts remain part of the release record.

The Modal bootstrap used for that check came from open PR #16 head `1fe461ac731c2e1d7ad9886ca5e766c430c5840b`. The exact executed [canary source](canary-source/) pins the new worker digest and adds a redacted child-error excerpt to the frozen boot harness. PR #16 remains owned by another task and has not been merged into main, so the main-branch Modal launcher still needs that integration.

## Fresh-point load and drain

The [bounded load receipt](fresh-capacity-2l4.json) and [plan](fresh-capacity-2l4-plan.json) used two L4 workers for 600 seconds against ECCp-79 with separate persistent volumes and run IDs. This tests twice a proposed one-L4 arrival envelope under the current two-auditor service setting:

| Measurement | Observed |
| --- | ---: |
| Distinct accepted and indexed points | 458 |
| Immutable raw S3 objects | 410 |
| Mean arrival and indexed rate over 600 seconds | 0.763 records/s |
| Maximum per-run admitted versus indexed lag in 20 samples | 1 record |
| Maximum sampled visible / in-flight audit queue | 0 / 1 message |
| Worker upload-pause events | 0 |
| Final run state | Both fully indexed and closed |

The [object audit](fresh-capacity-objects.json) fetched all 410 raw S3 objects, checked each key-bound SHA-256 digest, then used the pinned repository codec to check every point's curve equation, distinguished-point predicate, and `R=aG+bQ` witness. It found 458 unique point coordinates with zero duplicates. This recheck uses the verifier's arithmetic library; claimed trail-step counts are not independently verified by it.

A [read-only mid-run pressure snapshot](fresh-capacity-pressure-mid.json) and [post-run snapshot](fresh-capacity-pressure-after.json) both showed zero pending v2 batches, zero pending bytes, and zero active processing leases; the post-run audit queue was empty. The [CloudWatch window](fresh-capacity-cloudwatch.json) retained minute-level RDS and SQS series: maximum RDS CPU 24.82%, maximum 13 database connections, maximum SQS oldest-message age 0 seconds, and maximum minute-level visible messages 1. The controller ended both sandboxes at the time bound (exit 137), after which their committed checkpoints were read; all accepted records indexed and both runs closed.

This is a measured ECCp-79 one-L4 operating envelope with a two-L4 load check. It does not establish throughput for the other 23 campaigns, a larger fleet, global 503 behavior, recovery under a fresh-point verifier restart or injected SQS send failure, or actual billed cost. The effective Fargate quota was 30 vCPUs; the request for 256 remained `CASE_OPENED`, so the configured 32-auditor ceiling was not available in full. Keep public multicurve enrollment gated until those remaining release conditions are measured.

The temporary contributor key used here was kept out of these artifacts. It was disclosed in chat, so it should be revoked through the website dashboard. Its added private v2 allowlist entry was removed after the run; the preexisting private canary entry was retained.
