# Admission, abuse limits, and drain capacity

## Protocol scope

This service has two isolated protocols. The sections below describe the legacy
ECC2K-130 DP32 path. The generic v2 path supports all 24 published Certicom
instances using 56–196-byte affine coefficient records; see
[v2 storage and recovery](multicurve-storage.md).

V2 uses tiny authenticated JSON control messages at
`https://ecc-challenges.com/v2/`. Checksum/length/type/encryption-bound S3 PUT
capabilities carry the raw bytes directly, so large uploads do not occupy web
tasks. Every record must satisfy the namespace's DP predicate, curve equation
and `R=aG+bQ` relation before any DynamoDB index write. UUID runs and hashed
curve/target/walk/codec namespaces isolate the campaigns. Claimed step counts
are not credited as verified work.

V2 admission limits include eight outstanding reservations and 32 pending batches
per account, eight active runs across both protocols, the shared daily byte
quota, and a sharded global cap initially 512 pending v2 batches. Rejected new
intents receive 429 (account) or 503 (global shard) with `Retry-After`; they do not
obtain an upload capability. Admitted batches remain recoverable from immutable
S3 objects, PostgreSQL state and receipts even if SQS delivery or a worker fails.
The reconciler repairs stale unfinished work. Completed runs can be explicitly
closed without recycling identity or discarding admitted data.

The **audit** service performs v2 verification, conditional DynamoDB indexing
and collision resolution. Increasing the legacy index or resolver pools adds
no v2 drain capacity. V2 capacity planning must measure complete verification
and indexing per curve/record width, source-object and receipt requests,
PostgreSQL coordination, DynamoDB writes and duplicates, queue age, autoscaling,
and total cost. `scripts/capacity.py` only models legacy sampled replay and must
not be used to estimate v2 capacity.

The live all 24 single-point canaries establish contribution correctness. A
bounded duplicate-record backlog/recovery check establishes only its stated
admission and restart behavior. Neither defines a sustained fresh-point rate or
a global GPU fleet size. The configured 32-auditor maximum also depends on the
account's effective Fargate vCPU quota; it is 30 before the pending 256 request.
Public opening must name the measured operating envelope and retain admission
caps rather than treating autoscaling as unlimited capacity.

## Legacy protocol

The public endpoint is `https://ecc-challenges.com/v1/` on an AWS Application
Load Balancer in `us-west-2`, behind AWS WAF and two Global Accelerator static
addresses. Squarespace remains the DNS provider. ECS web tasks are stateless;
the dedicated private `rho_dp` PostgreSQL instance stores cursors and indexed
public candidates, while the new account's S3 bucket holds raw batches.

## Trust boundaries

| Input or asset | Boundary and control | Meaning after control |
| --- | --- | --- |
| GitHub identity | OAuth callback, secure signed session, CSRF on key forms | Contributor can manage only their account's keys |
| API key | 256-bit secret shown once, HMAC digest with Secrets Manager pepper, constant-time comparison, revocation | Authenticated upload principal; no cloud credential |
| Run ID | Isolated S3 seed registry uses conditional create | Seed namespace is reserved before assignment |
| DP upload | 1 MiB body maximum, structural checks, cursor lock, immutable checksum S3 write | Bytes are durably archived, not mathematically validated |
| Replay | Post-seal random selection; first batch mandatory; exact pinned host walk | Sampled record either matches, fails, or stays unverified |
| Collision | Both trails replayed, pinned host scalar solver, separate golden-model scalar multiplication, result receipt in S3 | A verified scalar requires both independent point checks |

The WAF blocks more than 10,000 requests per 5 minutes from one IP, and more
than 300 per 5 minutes for authentication or key routes. Both rate rules return
HTTP 429 with Retry-After 60, so shared-provider throttling is retryable rather
than a permanent permission error. The application has
a 240-token per-key burst bucket refilling 4 tokens/s; a 1 MiB DP upload costs 16
tokens. A key is limited to 5 GiB/day across DP batches and checkpoints, and an account to three active keys, eight active runs, and 64 lifetime run IDs. The pending batch counter is sharded 16 ways and admission
returns `503` with `Retry-After` when the configured backlog bound is hit.
The contributor sends a remote checkpoint at most every 15 minutes by default
and pauses its GPU when the local spool fills rather than discarding unsent
records.

These limits make abuse bounded per key and IP, but they do not prevent an
attacker from acquiring many GitHub accounts or using many networks. WAF and
per-key quotas are cost controls; the independent replay and candidate
quarantine protect result integrity. A determined attacker can still consume
storage and verifier capacity. Operators must watch WAF blocks, account
enrollment, S3 growth, queue age, and daily AWS spend, and lower quotas or
temporarily close admission if those diverge from the measured plan.

The current public run allocation is `50000..65534`, or 15,535 unique run
IDs over the lifetime of this campaign. IDs are never recycled because the
seed is `(run_id << 48) | local_counter`. The lifetime account cap limits one
account's ability to consume that finite namespace; operators can adjust it
after reviewing contributor needs and remaining IDs. A larger contributor population
needs a registry audit for additional unused IDs or a new campaign/seed
format; autoscaling HTTP tasks cannot expand this namespace.

## Queue and database design

The ingest ACK follows an S3 conditional `PutObject` plus one PostgreSQL
transaction for the cursor, quota, pressure count, and outbox row. A retry at
the same byte offset and digest is idempotent; a different digest gets `409`.
An outbox reconciler repairs a lost SQS send after the database commit. SQS
delivery may repeat, so audit, index, and resolve workers use durable status
rows and unique constraints. Audit, index, and collision each have their own
queue and dead-letter queue. Audit and indexing scale independently, avoiding
large DP joins on the HTTP request path.

Admission checks whether a pressure shard is full before the S3 write, then
atomically increments its count after the bounded write. The shard row is not
locked across S3 network I/O, so unrelated runs can upload concurrently. If
another upload fills it between those checks, the request returns `503`; its
write-once object can be reused by an exact retry after capacity clears.

The candidate index has 32 hash partitions on the 24-byte orbit key. Each
index task bulk copies one sealed batch into a temporary table, joins that
batch to the new account's `public.distinguished_points` projection through
its `(campaign_id,point_key)` key, and inserts public candidates idempotently.
That historical projection starts empty until the later verified import and
backmatch; current collision lookup covers new public points against each other.
The historical table is read-only to the worker role. A failed later audit
marks its run's prior candidates disqualified and its leads quarantined.
Source objects and audit receipts stay immutable for review.

## Capacity gate

Let `G` be active GPUs, `s` measured walk steps/GPU/s, `L` measured mean steps
per DP, `B` records/batch, `q` measured index rows/task/s, `T` measured seconds
to replay one selected record, and `R` new runs/hour. Approximate rates are:

```text
DP arrival/s       = G·s/L
batch arrival/s    = G·s/(L·B)
audit selections/s = R/3600 + max(0, batch arrival/s - R/3600)/128
index task need    = ceil(headroom · DP arrival/s / q)
audit task need    = ceil(headroom · audit selections/s · T)
```

Use `scripts/capacity.py` with measured values and at least 2× headroom.
Autoscaling targets are capped at eight index tasks and 32 audit tasks. Those
caps protect the private RDS instance and cost; exceeding them requires a
deliberate measured change, not a larger queue. The service returns backpressure
before the pending bound is exceeded. A queue that grows continuously at the
projected peak is an admission failure even if HTTP uploads still return 201.

Before a global opening, run a sustained, production-shaped load on the
release images with distinct valid run IDs and sealed DP32 objects. Record
arrival and drain rates over the same interval, p50/p95 replay seconds,
index rows/s, S3 request error rate, RDS CPU/IOPS/locks/connections, ECS
scale events, queue visible/in-flight/oldest age, dead-letter counts, end to
end bytes, and actual cloud cost. Repeat after a task restart and an injected
SQS send failure. Require the queues to return to baseline after traffic
stops and every acknowledged object to be indexed or explicitly retained as
unverified/quarantined. Compare the measured peak to the intended GPU fleet;
record the largest fleet that passed. The current DP32 replay throughput and
this sustained gate are tracked in the linked problem record.

## Database network boundary

The public service uses a private RDS instance in its dedicated AWS account
and VPC. PostgreSQL ingress is limited to that VPC's CIDR, and both service
roles use verified TLS and limited SQL privileges. The original campaign's
`rho-dp` security group is outside this platform and remains a separate
legacy exposure; it should be narrowed through a coordinated inventory of
its existing ingesters. The public service has no network route or credential
to that owner database.
