# All-curve distinguished-point storage

Implementation status: v2 is being integrated and is disabled by default. The
production ECC2K-130 v1 walk keeps its existing encoding and namespace. A catalog
entry alone never enables a worker. The ECC2K-130 v1 historical import is separate; see operations.md.

## Scope and identities

Support covers the 24 instances in Appendix A of the [official Certicom
challenge document](https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf):
9 prime-field and 15 binary-field instances, with actual field widths 79–359 bits.
`src/ecc_challenges/multicurve/instances.json` pins the PDF, extracted text, exact
polynomial basis, coefficients, G, Q, subgroup order and cofactor. Recorded source
normalizations are reproducible with `extract_parameters.py`. Probable-prime
checks are arithmetic admission checks, not primality certificates.

A namespace SHA-256 binds the exact instance digest, target Q, walk, DP predicate,
coefficient derivation and record codec. UUID runs and 64-bit trail IDs remove the
legacy 16-bit run allocation constraint. No record crosses namespaces, including
records for the same curve using different walks. The generic affine walk uses
32 deterministic nonzero G multiples; start coefficients use full-width SHAKE256
rejection sampling. Binary arithmetic uses the published polynomial basis.

Each record stores `trail_id:u64le | steps:u64le | x:BE | y:BE | a:BE | b:BE`,
where `R=aG+bQ`. Widths are fixed per namespace, derived from field and subgroup
sizes: 56–196 bytes for this registry. Scalars are binary, avoiding database
numeric precision limits. Finite points whose SHA-256 of canonical x||y has trailing DP bits zero qualify; the first hash byte selects the walk partition.
V1 uses the Frobenius Hamming-weight predicate with per-run limits 30, 32 or 34;
new NVIDIA sigma runs are recommended to use 34. Its thresholds are distinct
from V2's hash-bit predicate and never change an existing run.

DP bits are set per curve (`dp_bits_for` in `multicurve/codec.py`). This walk
has no automorphism quotient, so a solve takes about E = sqrt(pi*n/2) steps.
The rule is d = floor(log2 E) - 23, capped at 34. With up to 2^16 walks running
at once, the steps left in unfinished walks stay under 1% of E, and each curve
below the cap needs about 2^23.5 (~12 million) DPs. ECCp-79 gets 16 bits,
ECCp-109 31, and every curve of 130 bits or more 34 (ECC2K-130 about 2^30.8 DPs,
ECCp-131 about 2^31.4). The DP bit count is part of the namespace, so changing
the rule starts new namespaces; existing runs keep the setting pinned in their
manifest.

### Walk versions

A namespace names its walk, so records of different walks never meet. Walk 2
(`certicom-affine-radding32-v2`, the SHA-256 walk above) is defined for every
curve and stays valid: runs registered under it keep their namespace, manifest
and verification. ECC2-131 and ECCp-131 register new runs under walk 4 by
default (`DEFAULT_WALKS` in `multicurve/codec.py`); `Namespace(campaign,
walk_version=2)` still builds the old namespace.

| Walk | Partition | DP test | Group |
| --- | --- | --- | --- |
| 2 | first SHA-256 byte of x‖y, low 5 bits | trailing DP bits of the digest | points |
| 3 `…-xonly-v3` | `((x mod 2^64) * 0x9e3779b97f4a7c15 mod 2^64) >> 59` | bits 64..64+d-1 of x are zero | points |
| 4 `…-negmap-v4` | as walk 3 | as walk 3 | classes {P, -P} |

Walk 4 stores the canonical point of each class (prime: `y <= (p-1)/2`; binary:
`y <= x XOR y`, since -(x, y) = (x, x + y)) and negates `a` and `b` together with
the point, so a stored record still satisfies `R = aG + bQ` exactly. The
collision rule, the scalar recovery `(a1-a2)/(b2-b1)` and the DP codec are
unchanged; a collision of P with -P is the same stored point. A trail's `b` is
its start's `b` or `n - b`, which `check_trail` accepts for walk 4 only. Because
the group is halved, a solve takes about sqrt(pi*n/4) steps. A canonical walk
can fall into short cycles; the engine detects 2-cycles and cycles that return to
a periodic anchor and leaves them through a 33rd jump (the escape jump) at the
cycle's smallest x, so walks that share a cycle also share its exit. Cycle state
is local to one engine call and does not enter any record.

Every trail keeps the `b` of its start, because jumps add only multiples of G.
Verification therefore also checks that each DP's `b` equals the start derived
from its run ID and trail ID, which ties the DP to a server-assigned trail
without replaying the walk. It does not check the trail's `a` or step count.

## Submission and processing

```mermaid
flowchart LR
    C[Contributor container] -->|API key + intent metadata| A[HTTPS admission API]
    A -->|reserve quota and backlog| P[(PostgreSQL metadata)]
    A -->|short-lived signed PUT| C
    C -->|immutable bytes + checksum| S[(Versioned private S3)]
    C -->|authenticated commit| A
    A -->|HEAD size + checksum + version| S
    A -->|immutable admission receipt| S
    A -->|transactional outbox| P
    P --> Q[SQS verification queue]
    Q --> V[Bounded verification workers]
    V -->|full point relation checks| S
    V -->|conditional exact-point insertion| D[(DynamoDB point index)]
    D --> L[Collision lead + scalar verification]
    L --> S
    S --> X[Sorted compact segments / index rebuild]
```

1. An authenticated upload intent reserves a byte quota and a backlog slot before
   issuing any S3 capability. At most one reserved intent exists at a run cursor,
   and at most eight exist per account. Intents expire after 15 minutes.
2. A signed PUT fixes object key, content length, SHA-256 checksum, content type,
   server-side encryption and `If-None-Match: *`. Contributors receive no AWS
   credentials and cannot list, read, delete or choose archive keys.
3. Commit reauthenticates the API key and run, checks the uploaded length and
   checksum with HEAD, and pins the S3 VersionId. Immutable batch and admission
   receipts describe the accepted bytes. Successful acknowledgment requires the
   admission receipt. A durable database outbox repairs interrupted queue sends.
4. A verifier checks the raw SHA-256 and namespace binding, canonical coordinates
   and scalars, DP predicate, curve equation, and **every** `R=aG+bQ` relation.
   Only fully validated batches produce verified receipts and index writes.
   A lease token guards progress and terminal updates. Retry cannot skip an
   unfinished record or use another verifier's uncommitted progress.
5. Each exact point uses a conditional DynamoDB PutItem with
   `attribute_not_exists(pk)` and `ReturnValuesOnConditionCheckFailure=ALL_OLD`.
   The prior witness is returned on a collision. This avoids a lookup/insert
   race and preserves the first immutable witness. Identical coefficient pairs
   are duplicate work; distinct pairs create deterministic collision leads.
6. A batch is indexed only after all point inserts and collision leads are
   durable. For equal points, solve `(a1-a2)/(b2-b1) mod n`, then check `dG=Q`.
   A zero denominator is recorded as degenerate. All result receipts remain in
   S3. A retry after an index insert recreates any interrupted lead.

SQS is at least once: leases, immutable object keys and idempotent records are
required. SQS depth is not an authoritative ledger. Reconciliation requeues
unfinished database rows, including messages that expired or reached a DLQ.

## Archive layout

```text
public-gateway/<stream>/v2/
  namespaces/<namespace>/manifest.json
  raw/<namespace>/shard=<sha256-prefix>/runs/<uuid>/<byte-offset>-<sha256>.bin
  batches/<namespace>/<batch-uuid>/<receipt-sha256>.json
  admissions/<namespace>/<batch-uuid>/<receipt-sha256>.json
  checkpoints/<namespace>/<batch-uuid>/<receipt-sha256>.json
  verified/<namespace>/<batch-uuid>/<receipt-sha256>.json
  collisions/<namespace>/<lead-id>/<receipt-sha256>.json
  compaction/<namespace>/<segment-id>/<receipt-sha256>.json
  segments/<namespace>/<segment-sha256>.bin
```

Raw objects and receipts use conditional writes and versioning. Service roles
have no object deletion permission. Bucket policy enforces conditional writes
for v2. S3 remains the witness archive; PostgreSQL holds account/run/cursor,
quota, processing and collision metadata. DynamoDB is a rebuildable online index.
PostgreSQL account secrets and quota history still require database backups.

Do not create one S3 object per DP. Contributors upload batches up to 1 MiB;
compaction groups verified batches into sorted segments targeting 128–256 MiB.
A segment retains the source batch UUID and record ordinal for every witness,
plus a manifest naming all source checksums and versions. Compaction never
removes raw witnesses. Lifecycle rules may move old compact segments to cheaper
storage after their minimum storage and access requirements are understood.

## Compact point index

`POINT_INDEX_MODE=compact` replaces the DynamoDB index with sorted 16-byte
entries in S3 (`multicurve/pointindex.py`). Uploads and verification are
unchanged: workers still send full records, every record is still checked, and
the raw batch stays the authority. Only the index is smaller.

```text
public-gateway/<stream>/pointindex/<namespace>/parts/<part>-<sha256>.bin
public-gateway/<stream>/v2/pointindex/<namespace>/generation-<n>/<receipt-sha256>.json
```

| Bytes | Field | Meaning |
| --- | --- | --- |
| 0–7 | fingerprint | first 8 bytes of SHA-256(x‖y), the digest the walk already computes |
| 8–15 | locator | `index_seq` (40 bits) ‖ record index (24 bits), big-endian |

1. Indexing a verified batch only gives it the next `index_seq`
   (`dp_index_seq`, in PostgreSQL). Nothing is written per point.
2. The reconcile task merges every `POINT_INDEX_MERGE_SECONDS` (or run
   `python -m ecc_challenges.queues merge` once). For each namespace with
   pending batches it reads their raw objects (version- and SHA-256-pinned),
   makes one entry per distinct (point, witness), and merges them into parts:
   4,096 sorted files, one per top 12 fingerprint bits.
3. Each new fingerprint is looked up in its part. A shared fingerprint is
   resolved from the full records: the same point with another witness becomes
   a collision lead and is solved as before; the same witness is duplicate work,
   whose entry is dropped and counted as deduplicated; a different point is a
   fingerprint false positive (about N²/2⁶⁵ of them for N entries, so roughly
   0.1 for a full ECC2K-130 solve).
4. A generation manifest (parts, checksums, counts, leads) is written under
   `v2/`, then committed by compare-and-swap on `dp_point_index`, together with
   the batches it covers and their duplicate counts. Parts the new generation
   replaced are deleted; a lifecycle rule expires their old versions after 7
   days. Parts live outside `v2/`, which forbids deletes.

The index is derived. `Merger.rebuild(namespace)` discards it, and the next
merges rebuild it from every indexed batch; counters are adjusted by the
difference, so a rebuild leaves them unchanged. Batches indexed while the mode
was `dynamodb` have no `index_seq` and stay in DynamoDB only.

Sizing: a merge rewrites every part that received entries, so its cost grows
with the index (16 bytes per entry). ECC2K-130 at DP34 is about 2^30.8 entries,
about 30 GB in S3 (under $1 a month) against about 1 TB of DynamoDB items.
Merging the part lists is fast (30 million existing plus 1 million new entries
in about 4 s of CPU); S3 transfer dominates. Below about 100 million entries
hourly merges are cheap. Above that, merge daily (`POINT_INDEX_MERGE_SECONDS=86400`).
A collision is found at the first merge after both points are indexed.

## Capacity, abuse and accounting

API tasks scale behind the existing HTTPS load balancer. Direct S3 upload keeps
DP bytes off web task memory and network paths. Verification and conditional
index insertion scale independently from admission. Limit in-flight work per
worker; do not submit an unbounded future per point. Autoscale against oldest
queue age and sustained backlog, and monitor verifier rate, index throttle
count, failed validation, duplicate rate, DLQs and reserved-byte utilization.

Admission stops issuing capabilities when bounded pressure pools fill and
returns Retry-After. Clients retain pending records and checkpoint state, pause
compute during failed upload, and retry with backoff. v1 and v2 have separately
bounded pressure pools; their sum is the overall maximum. Keys share an account
run cap and daily byte accounting; there is no anonymous ingestion route.

Full algebraic verification establishes a valid point relation. It does not
prove the submitted path, elapsed GPU work, independence, or claimed step count.
An attacker can still send duplicate valid witnesses; deduplication and quotas
bound their impact. Do not convert reported `steps` into verified work. Progress
percentages remain unknown until a declared work-verification and collision
model exists. Audited walk segments or unpredictable replay challenges are a
separate future scoring gate.

For capacity planning, `raw bytes/day = 86400 * DP/s * record_bytes` before
compression and replicas. Conditional index write units/day are approximately
`86400 * DP/s * write_attempts_per_DP * ceil(item_bytes/1024)`; failed conditions
and retries also consume capacity. At 10,000 DP/s, raw records alone are
48.384–169.344 decimal GB/day across current widths. This is arithmetic sizing,
not a measured ingest rate. Every-curve verification cost, provider GPU controls,
and sustained end-to-end capacity must be measured before advertising a rate.
Prewarm DynamoDB capacity and request quota increases for the intended launch
rate; on-demand mode does not guarantee an unlimited instantaneous burst.

## Required launch checks

- Exact registry validation and all ten published scalar correctness vectors.
- All 24 native CPU transitions checked against the independent Python group
  implementation, including wide limbs, identities and malformed encodings.
- The same finite controls on a real NVIDIA GPU using the published image.
- Presigned mutation rejection, quota/backlog boundaries, revoked keys and
  cross-namespace submissions, plus concurrent first-point collision controls.
- Interrupted upload/commit/verification/indexing and expired-message recovery.
- Archive-driven index reconstruction and compaction checksum verification.
- Measured sustained ingest with declared DP/s, item widths, verification rate,
  queue-age limits, duplicates, retries, cost and headroom.

Sources: [DynamoDB PutItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_PutItem.html),
[S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[S3 HEAD checksums and versions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html),
[SQS delivery semantics](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues-at-least-once-delivery.html),
[DynamoDB on-demand scaling](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/on-demand-capacity-mode.html).

## Operator tools

The exact verified receipt list is an explicit bounded input, in a JSON array.
Use a role scoped to the archive and the separate rebuild table. The tool checks
its AWS account before any mutation. A rebuild checks every relation again,
preserves every collision in S3, and emits completion only after durable leads.

```sh
python -m ecc_challenges.multicurve.archive_tool compact \
  --profile ecc-challenges-new --expected-account 104186811093 \
  --bucket ecc-challenges-data-104186811093 --campaign eccp-131 \
  --verified-keys /path/to/verified-keys.json --max-input-mib 128
python -m ecc_challenges.multicurve.archive_tool rebuild \
  --profile ecc-challenges-new --expected-account 104186811093 \
  --bucket ecc-challenges-data-104186811093 --campaign eccp-131 \
  --verified-keys /path/to/verified-keys.json \
  --target-table ecc-challenges-rebuild-20261016 --max-input-mib 128
```

Provision the destination rebuild table with the same `pk` string schema.
The operator must compare complete source coverage and collision receipts
before switching a service to the rebuilt table. Account/run metadata comes
from PostgreSQL backups; the index tool does not restore OAuth or quota history.
For archives beyond one256MiB group, run disjoint explicit receipt groups against
the same destination with `--resume`; retain every group's completion receipt.

Verification workers bind the DynamoDB creation UUID (`TableId`) to a durable
PostgreSQL row on their first startup, before any batch has been indexed.
A table recreated under the same name fails this fence. Worker roles can insert
and read the binding but cannot update or delete it. Rebuilds target a separate
name; only an operator can activate a complete rebuilt generation after checking
all completion and collision receipts. Stop workers during that operation.
