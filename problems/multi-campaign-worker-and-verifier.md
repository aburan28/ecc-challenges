# Certicom multi-campaign worker and verifier

Status: open. Scope: admitting distinguished points for the 23 published
Certicom exercises and challenges other than ECC2K-130 through the
`ecc-challenges.com` public gateway.

## Binary arithmetic throughput obstruction, 2026-10-19

The nine binary campaigns have integrated worker code and finite correctness
controls, but the fastest shared arithmetic for sustained Pollard rho work is
not established. A bounded-word, branchless `binary_xtime` candidate was
correct on the pinned controls yet slower than the current full-width
implementation on a matched NVIDIA L4 panel. All 18 long-panel candidate
invocations took longer relative to their bracketing baselines; the pooled
candidate/baseline full-process median was 1.208. The ten comparisons whose
baseline bracket drift stayed within 1.05 had ratios 1.122–1.360. The candidate
was removed from the release diff. See the [insight report and raw receipts](../verification/all-curves-20261016/xtime-20261016/main-adaptation/l4-insight-20261019.md).

This observation establishes a regression for that candidate on those L4
workloads. It does not identify the cause or prove the present arithmetic
optimal.

### 2026-10-21 CUDA bit-length update

A CUDA-only leading-zero intrinsic for `bit_length` passed exact worker output,
distinguished-point and step replay across the nine pinned binary campaigns.
Its native L4 A/B panel had 13 stable baseline brackets out of 18, all 13
favoring the candidate; the stable-pair median full-process time ratio was
0.839 and the all-pair median kernel ratio was 0.730. The identical-binary A/A
control had stable-pair median 1.004 and kernel median 1.000. Native resource
output reported 160 candidate registers versus 159 baseline, unchanged stack
and no spills. The first PTX A/B panel also favored the candidate in 11 of 12
stable pairs. Exact commands, raw receipts, verifier, plots and PDFs are in
[the arithmetic selection record](../verification/binary-rho-profile-20261021/arithmetic-selection.md).
The change is retained as the faster tested arithmetic for this L4 worker
workload, not as an assertion of global optimality.

The exact-commit public worker image was subsequently built, checked on an L4
against all 168 archived CUDA controls, and advertised by the live gateway.
The nine requested binary curves passed 63/63 of those controls, each with
nonempty positive DP1 output. The web rollout completed with two healthy
targets; see the [published-image release record](../verification/binary-rho-release-20261021/release-insight.md).
This closes the image-publication and finite final-image correctness steps for
the optimized arithmetic. It does not close the sustained nine-curve GPU
contribution or complete-campaign performance obstruction.

The long DP40 timing panels emitted zero distinguished points, so their DP
hash parity concerns empty outputs. A separate optimized-source L4 check passed
168 frozen CUDA controls across all 24 curves, including positive DP1 emission
for every curve; 137 DP1 records were checked among the nine requested binary
curves. The [positive-DP report and receipt](../verification/binary-rho-profile-20261021/positive-dp-insight.md)
and [boundary correction](../verification/binary-rho-profile-20261021/dp-boundary-correction.md)
keep that finite correctness result separate from sustained DP40 density.

Sustained useful-step rate, point collection/submission, server verification,
restart cost and other hardware remain unmeasured. The next discriminating
test is a sustained nine-curve GPU canary with matched current-image controls,
recording useful steps, accepted points, wall/GPU time, bytes, verifier cost
and restart behavior, with an A/A control and exact output checks. A passing
full pipeline comparison would support a campaign-level improvement for its
measured scope; a regression would identify the stage that erased the worker
gain. Only that complete comparison can establish a campaign-level gain, so
this performance requirement remains open.

## Current release boundary, 2026-10-16

All 24 published instances now have implemented CPU/CUDA workers and the v2
archive/verifier/index path. The selected published image passed 168 checks on
a real L4 as UID/GID10001. All 24 production-predicate CPU contribution checks
also passed against the deployed gateway: upload, index, checkpoint resume,
and explicit run closure. Exact image/source/S3-version bindings and failed
attempts are retained in [release evidence](../verification/all-curves-20261016/README.md).

The remaining obstruction is release validation: a real GPU contribution with a
persistent volume, current-head required CI, and a sustained fresh-point capacity
envelope. Anonymous GHCR import and all 168 L4 checks passed, and the bounded
account-backlog/refusal/restart/drain check passed. The AWS account currently has 30 Fargate
vCPUs; request `6b9319d443fe49b882919fe6ebcfeef4QPzCozkp` asks for 256. Until
effective, the configured 32-auditor ceiling is not available in full.

At the 2026-10-16 checkpoint, V2 remained restricted to the designated canary
key while those gates were checked. The 2026-10-21 live catalog snapshot linked
above reports `multicurve_enabled: true`; this later operational state does not
by itself satisfy the outstanding per-curve performance measurements.
Completion probability remains unavailable because coefficient validity does
not verify the claimed number of trail operations. The historical database
import ran on 2026-10-17; see docs/operations.md, "Historical point import".

### Modal checkpoint access obstruction

After the release PR merged, a CPU-only probe of the selected public image on
Modal failed to create `/data/probe.tmp` as UID/GID10001 after a successful
root bootstrap `chown('/data', 10001, 10001)`. No contributor key was supplied.
The failure log and receipt are preserved as the first volume-readiness attempt.
The local harness reported IndexError while parsing empty stdout; the remote
stderr identifies PermissionError13 as the actual checkpoint-write failure.

Modal documents that it ignores Docker USER. Its public Volume documentation
does not specify whether mount ownership changes permit another UID to access
the filesystem. A mount-level UID restriction is a suspected explanation,
not an established diagnosis. This blocks the Modal launcher's persistence
guarantee and the real GPU contributor canary; it does not invalidate the
completed GPU arithmetic or Fargate contribution checks.

The next bounded diagnostic creates a private child directory as root, records
ownership and modes before/after changes, and attempts atomic writes as10001.
Acceptance requires two fresh CPU sandboxes to read and advance the same
mode0600 checkpoint as UID/GID10001, plus an external committed-volume hash
match. An access failure requires a different verified persistence mechanism;
repeating chmod/chown without changed evidence is insufficient. A passing
probe permits a launcher repair and the separately authorized GPU canary.
Sustained fresh-point capacity remains a separate outstanding gate.

The final shared-bootstrap attempt5 met this filesystem acceptance criterion:
two fresh CPU sandboxes advanced the same mode0600 file as UID/GID10001, both
external committed-volume hashes matched, and the original mount-root file
migrated successfully. Its frozen setup SHA is
`9211407a376609a5789aa62b2d2dd5ca1d41730d41ad0dd52e92fb8b55f3704a`.
The provider `/data` entry was observed to be a symlink. Resolving that trusted
entry and creating a private owned child corrected access; persisted child
symlinks remain rejected. The first failed production-helper attempt rejected
the provider mount symlink itself; it is preserved alongside the earlier
failure and successful diagnostic controls in
[volume-readiness evidence](../verification/all-curves-20261016/release/modal-volume-readiness/README.md).

This closes the checkpoint-permission obstruction for the tested Modal image
and shared bootstrap. The real GPU contribution still requires the separately
requested authorization to transmit the contributor key through a runtime
Modal Secret. No contributor key was sent during these filesystem controls.

### Consumer-stop and queue-baseline check

The first bounded pressure attempt rejected a valid regionless S3 hostname in
the harness; its held reservation was completed and indexed normally. The
second attempt observed ECS runningCount 0 while the old tasks were STOPPING.
Their processes still consumed records, so the intended 32-batch backlog never
formed. All 33 acknowledged records subsequently indexed through normal commit
replay, with no lease mutation; that recovery receipt is retained separately.

The next preflight refused a nonzero SQS in-flight count. A read-only SQL task
then measured 59 indexed v2 batches, zero pending v1/v2 batches or bytes, and zero
active processing leases, while SQS retained two invisible deliveries. This is
a delivery-versus-work accounting distinction. The raw SQS count alone cannot
establish a pending application backlog. No message was purged or deleted to
make the check pass.

The corrected bounded check must wait for the exact old task ARNs to become
STOPPED. Any nonzero in-flight baseline requires a fresh, source-bound read-only
SQL receipt with zero unfinished work/pressure and matching queue counts.
Acceptance then requires 32 new admitted records, the precise account-cap 429 on
the next intent, normal restart/drain, successful retry, stable commit replay,
closure and restored service/scaling settings. A final SQL snapshot checks
pressure returns to baseline. This remains a duplicate-witness engineering
check; sustained unique-point capacity and global 503 behavior require separate
tests.

Attempt 4 met that bounded acceptance criterion. Its separate before/after SQL
receipts show zero pending bytes/batches for both protocols and zero active v2
processing leases. This closes the bounded account-refusal and consumer-restart
check; it leaves fresh-point capacity, global 503 and the other release gates
open.

## Original obstruction (preserved history)

The public CUDA image, 32-byte DP record, run ID and seed namespace, S3
registry, PostgreSQL constraints, replay program, and collision resolver
implement the ECC2K-130 Frobenius walk. The other Certicom entries include
different binary-field and prime-field curves. A point or seed from one of
them cannot be checked by the ECC2K-130 replay program or safely indexed under
its key. The separate `pool/` code is a prime-field prototype with generated
sample curves and a single SQLite server; it has neither the published
Certicom parameters nor this gateway's durable admission pipeline.

The site now catalogs all 24 names, and the API rejects incompatible campaign
registration with HTTP 409. This is a missing implementation and validation
boundary. It is not evidence that the other challenges are infeasible or that
a generic worker cannot be built.

## Exit evidence

For each campaign to be opened:

1. Freeze a source-linked exact parameter transcript for field, curve,
   subgroup, generator and target. Verify point membership and subgroup order
   with an independent implementation and known-answer cases.
2. Build a worker image with a named walk and checkpoint format, and an
   independent replay program for that exact walk. Demonstrate a provider
   neutral Docker run, plus bounded runs on at least one hosted GPU provider.
3. Isolate campaign, walk version and run identity in registration, object
   keys, database uniqueness, queues and collision resolution. Prove that a
   point submitted under another campaign cannot be acknowledged or indexed.
4. Complete a real contributor canary: issue a website key, register, upload
   records, independently replay selected trails, index an eligible batch,
   create a controlled duplicate lead, and independently verify a scalar
   result. Retain exact image, source and receipt digests.
5. Measure the release image's arrival and drain capacity at the planned
   fleet rate and show bounded backlog under the controls in
   [DP32 verifier and ingestion capacity](dp32-verifier-and-ingest-capacity.md).

Passing one campaign's checks opens only that campaign. It does not transfer
throughput, point validity, or solution status to the other 22.

## Original implementation sequence (preserved history)

Start with ECCp-131 and ECC2-131 because they are the other 131-bit Level I
entries in Certicom's 2009 document. Import the exact Appendix A parameters
and validate them independently. Implement one CPU reference walk and one
versioned wire envelope, then test a known-answer trail and a wrong-campaign
upload against an isolated database. Bound the run to one seed range, one
container, and one replayed batch before any public registration. A matching
trail and refused cross-campaign upload permit the provider canary; an
arithmetic mismatch returns to parameter or walk debugging. A resource
failure records the environment and leaves the mathematical status open.

The [campaign integration guide](../docs/campaigns.md) gives the route for
each subsequent curve. Certicom source:
<https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf>.

## Implementation evidence, 2026-10-16

The v2 implementation now includes exact parameters for all 24 instances,
fixed-width affine coefficient witnesses, UUID runs, SHAKE256 full-width seed
derivation, CPU/CUDA arithmetic, direct immutable S3 upload, full algebraic
verification, conditional DynamoDB collision indexing, and archive compaction
and index reconstruction. See [storage design](../docs/multicurve-storage.md)
and [frozen correctness receipts](../verification/all-curves-20261016/README.md).

All 24 parameter checks and 10 published scalar controls passed. The CPU engine
passed 72 transition cases; a real Modal L4 passed 168 comparisons/rejections over
all 24 curves. Isolated PostgreSQL/mocked AWS controls cover valid/rejected
admission, immutable retries, verification-before-indexing, crash recovery,
collision scalar verification, revocation, compaction and fresh-index replay.

This record remains open under the current release boundary above. The Linux
build, final-image finite GPU controls and all 24 CPU upload/index canaries have
since completed. Sustained arrival/drain measurements and a declared launch
fleet rate remain outstanding. Full algebraic verification does not attest
claimed trail lengths; the site's completion figures are point-count estimates
under an explicit model, and verified work counts remain outstanding.

Local image builds failed because Docker Desktop could not start. The system
volume also reached 100% utilization. Completed failed test artifacts were moved
intact to the project SSD, and subsequent isolated tests use `.tmp/` there.
Linux release builds are provided through `infra/build.yaml`; they must produce
pinned image digests before deployment. No failed receipt was overwritten.

### DP predicate correction

Before enabling v2, a finite control found every one of 32 sampled ECC2-79 G
multiples had odd x. Raw coordinate low bits therefore cannot be assumed to
supply the intended cross-curve DP density. The initial arithmetic parity tests
did not require nonempty DP output for every curve and missed this operational
failure. The superseded sources and receipts are preserved under
`verification/all-curves-20261016/raw-x-v1/`.

The generic replacement uses SHA-256 of full canonical x||y for both partition
selection and the DP predicate, with native job magic ECCJOB03 and a new walk
namespace. SHA-256's distribution is a walk-model assumption; the release
correctness gate now additionally requires finite DP1 emission on every curve.
All CPU and GPU transition controls must be rerun for this exact revision.
The legacy Frobenius predicate is a separate established protocol.

The corrected ECCJOB03 implementation passed the repeated 72 CPU cases and 168
real L4 comparisons/rejections across all 24 curves. Every curve's DP1 control
emitted points. Evidence: `verification/all-curves-20261016/cpu-job03.json`,
`cuda-job03-l4.json`, `job03-controls.tar.gz` and `source-job03-sha256.json`.
A designated key-ID preview gate permits the live canary without marking v2
publicly available. The bearer value is kept outside version control.

### Public image distribution limit

The combined image built successfully in CodeBuild at source `aaf1dad4b7561c47639b7583d072bfb05fbe5bed`.
Its public ECR digest is `sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee`.
Modal app `ap-IjZkwBeYyxtWpvLD9l6v0t` could not import it: the registry returned
`toomanyrequests: Data limit exceeded` before sandbox or GPU startup. The failed
receipt is retained under `verification/all-curves-20261016/release/`.
AWS documents a non-adjustable 500 GB/month anonymous consumer limit. A shared
provider egress pool is a suspected explanation; the receipt does not identify
which quota-accounting identity reached the limit.

This blocks a provider-neutral anonymous-pull release, not the arithmetic check.
The next steps are a temporary pull-only registry identity for the internal
canary and an independently accessible public mirror. Acceptance requires an
anonymous hosted-provider pull of the exact published digest, all 168 final-image
controls, and the normal entrypoint contribution check. Publishing an image or
successfully pulling it inside AWS does not meet that exit condition.
