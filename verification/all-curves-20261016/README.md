# Portable engine correctness controls, 2026-10-16

`release/build-auth-failure.json` preserves the first isolated Linux release
attempt, CodeBuild `b4a6b022-5d53-4717-ad16-14119c86c1a1`. It stopped before
image building because the conditional STS service-bearer-token permission did
not authorize ECR Public login. The builder policy now follows the unconditional
token permission in AWS's ECR Public managed policy; repository push permissions
remain restricted to the release repositories. This attempt supplied no image
or contributor evidence.

Current generic format: ECCJOB03, with SHA-256 point partitioning and DP selection. `cpu-job03.json` records 72 passing cases across all 24 curves, each with nonempty DP1 output. `job03-controls.tar.gz` preserves all exact inputs and expected outputs. `source-job03-sha256.json` binds the corrected implementation. `cuda-job03-l4.json` records 168 passing comparisons/rejections across all 24 curves on Modal app ap-wQybTDm1rDjAJZG7ECGE6R, NVIDIA L4 driver 610.57.04.

## Published image and live service

The selected contributor image is
`public.ecr.aws/o1a3z1g6/ecc-challenges-worker@sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee`.
`release/build-aaf1dad4b756.json` binds its immutable Linux build manifest.
The initial deployed web/audit/admin images are separately bound by
`release/build-3bf655be66af.json`. The mobile navigation fix is bound by
`release/build-5dc26c4a365e.json`; the selected contributor image remains the
separately tested digest above.

`release/final-image-l4-attempt-3.json` records all 168 final-image checks passing
on a real Modal L4, including all 24 curves and UID/GID10001. The exact native
binary SHA256 is `58cfe17630900eaf842d748ba66751a460b0219ec9c7fd59acd6cf4cd90dce23`.
Attempt 1 preserves the anonymous ECR import failure; attempt 2 preserves the
provider's ignored Docker USER and truncated receipt. Neither failure was
overwritten. Attempt 3 explicitly drops privileges and transports checksummed
receipt chunks.

`release/live-all24-cpu-attempt-1/plan.json` freezes the real contribution check,
with four concurrent Fargate containers at most. Its `summary.json` records
24/24 successful checks. Every curve used the real selected image's CPU
entrypoint, generated a production DP20 record, uploaded through the public
gateway's checksum-bound S3 capability, waited for indexing, resumed the same
checkpoint with advancing state, and closed its run. The complete public
checkpoints and sanitized task logs are retained beside each receipt.
`release/live-cpu-archive-binding.json` separately verifies the first canary's
raw S3 version, checksum, admission/verification receipts and DynamoDB witness.

These finite checks establish the tested correctness and contribution paths.
They provide no sustained fleet-rate measurement or verified trail-work count.
Public v2 remains gated while the real hosted GPU contribution, required CI and
sustained fresh-point capacity checks are completed.

The [Modal volume repair receipts](release/modal-volume-readiness/README.md)
preserve five CPU-only filesystem attempts. The final shared bootstrap passed
two fresh-container resumes with UID/GID10001, mode0600 files and matching
external volume hashes, including migration from the old mount-root layout.
The contributor API key was not supplied to any of those checks. The real GPU
contribution remains a separate pending release gate.


`release/ghcr-mirror-attempt-1.json` preserves the older Ubuntu 22.04 Skopeo
compatibility failure before image copying. The repaired workflow verifies the
completed copy's digest and anonymously hashes the newly published tag's raw
manifest before accepting a mirror.

`release/ghcr-mirror-attempt-2.json` records the successful mirror at
`ghcr.io/aburan28/ecc-challenges-worker@sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee`.
`release/anonymous-ghcr-l4-attempt-2.json` records a complete anonymous Modal
image import and168/168 L4 checks, without a registry secret. Its binary hash
matches the selected ECR image. The first anonymous-check wrapper attempt
failed locally because its virtualenv lacked the Modal executable; no remote
request occurred in that attempt.

## Bounded admission and consumer recovery

`release/live-backpressure-attempt-4/` freezes and records the successful
duplicate-witness check. After the exact old audit tasks reached STOPPED,
32 one-record batches remained admitted. The next intent returned the precise
account-backlog 429 with Retry-After 60 and no upload capability. Restoring the
two consumers indexed every acknowledged record; retrying the refused intent
then succeeded, commit replay retained the same admission receipt and cursor,
and the run closed. Total raw payload was 1848 bytes. This is an admission and
recovery control, not a fresh unique-point capacity measurement or global 503
test.

The read-only SQL snapshots `live-pressure-before-attempt-4.json` and
`live-pressure-after-attempt-4.json` record zero pending v1/v2 batches/bytes and
zero active processing leases before and after. The final snapshot contains
92 indexed batches. Two terminal SQS deliveries remained invisible in both
snapshots; no message was purged or deleted to create an empty baseline.

Attempts 1–3 and separate recovery receipts retain the harness's regionless-S3
hostname rejection, ECS STOPPING-versus-STOPPED mismatch, and refusal of a
nonempty queue baseline. Attempt 2's unexpected successful intent included a
signed upload capability. Its public result is explicitly redacted and hashes
the preserved private original; no capability is included in version control.

## Live collision and website checks

`release/live-published-scalar-collision-attempt-1/` records one 56-byte
constructed equivalent coefficient witness for Certicom's published ECCp-79
scalar. The deployed verifier indexed it, resolved the collision to that exact
published answer, and closed the run. The separate
`live-published-scalar-collision-binding.json` binds five versioned S3 receipts:
both source verification receipts, the collision lead/result and the index
checkpoint. The original DynamoDB witness binding remained unchanged. This is
a deployed correctness control using a supplied answer; its trail steps are
not verified and it supplies no new challenge-solving result.

`release/live-service-5dc26c4.json` records the completed website deployment,
selected anonymous GHCR image, HTTPS 200 responses for home/status/health/catalog,
and retained private v2 gate. `release/live-website-final/` contains actual
deployed screenshots and layout checks at 390 and 1440 pixels, with no stylesheet
injection. Both pages have 24 campaign rows; the status table has 10 aligned
columns. The earlier source-style layout checks additionally cover 320 and 768
pixels. The phone navigation overflow was corrected before these final checks.

The earlier `cpu.json`, `cuda-l4.json` and `source-sha256.json` below are superseded raw-coordinate predicate receipts. Their exact sources are retained under `raw-x-v1/`; those receipts establish arithmetic parity only for the earlier proposal.

`cpu.json` records 72 finite transition cases over all 24 published Certicom
instances. `cuda-l4.json` records 168 successful comparisons/rejections on an
NVIDIA L4 through Modal app `ap-O0t5St6b2trML1ZEAZWDSm`, driver 610.57.04.
All successful CUDA outputs matched the independently checked CPU outputs byte
for byte. Invalid infinity, coordinate, coefficient and exhausted step-counter
controls were rejected. `source-sha256.json` binds the actual sources and exact
parameter registry used for those controls. These are correctness receipts;
they contain no throughput estimate or solution-status conclusion.

CPU native arithmetic was compared with the independent Python affine group
implementation, including scalar limb boundaries through 352 bits, infinity
transitions, changing target coefficients, modular wrap and DP termination.
The parameter registry separately passed all 24 on-curve/order/field checks and
all 10 published scalar correctness vectors. Prime tests are probable-prime
checks, not formal primality certificates.

Reproduce finite controls:

```sh
c++ -x c++ -std=c++17 -O2 worker/generic/engine.cu -o /tmp/ecc-engine
/tmp/ecc-engine --self-test
PYTHONPATH=src python scripts/check_native_curves.py --cpu /tmp/ecc-engine --out /path/to/new-controls
ECC_NATIVE_CONTROLS=/path/to/new-controls modal run scripts/check_modal_curves.py
```

Each control retains its job, input states, output states, emitted points,
command, stdout and stderr under the selected output directory. The original
local run is retained at `/private/tmp/ecc-native-controls-20261016a`.
The final-image and live-service evidence above is separate from source-only
controls. Compiling this source on Modal does not verify a different image's
entrypoint or driver requirements. Collision correctness is independently
covered by the isolated service tests; a production controlled collision and
sustained arrival/drain measurement require their own receipts.

Two earlier Modal wrapper attempts failed before correctness execution: the
first omitted Modal Python image metadata; the second read a client-only
environment variable during remote import. Their app IDs are
`ap-PYwUGQP0IFbMZGZByLY5ui` and `ap-JxOwG4fngXpZZkGYNxTg1i`.
The source compiled and its CPU self-test passed in both builds. These failures
are retained as infrastructure outcomes; neither supplied arithmetic evidence.
