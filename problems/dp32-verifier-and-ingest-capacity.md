# DP32 verifier and ingestion capacity

Status: open. Scope: the `ecc-challenges.com` public ECC2K-130 admission
gateway at DP weight 32, using the pinned sigma walk, 1 MiB batches, a 1/128
post-seal later-batch audit selection, and the existing `rho_dp` instance.

## Specific obstruction

The exact host replay binary is built and wired to a separate SQS audit pool,
but its DP32 replay seconds per selected seed and the sustained audit/index
drain rate on release infrastructure have not yet been measured. The CPU walk
can require hundreds of millions of steps per report. Without the measured
service time and a production-shaped arrival/drain test, the configured 32
audit and eight index task ceilings cannot justify opening to an unbounded
global fleet. A growing queue would eventually trigger admission backpressure
and local worker spooling; the service would remain durable but would not keep
up with the offered load.

This is a resource and validation gap, not a proof that the design cannot
scale. The existing campaign already has distinguished points in RDS, but
their count does not measure this new verifier or public-gateway throughput.
The reserved public run range has 15,535 IDs, which also bounds the number
of distinct public run identities without a registry audit or new format.
The inherited world-open RDS ingress is a separate security exposure and does
not establish a throughput result.

## What would clear it

1. Capture a frozen DP32 seed/record pair from the same sigma campaign and
   exact source/image digests. Replay it independently on the release audit
   task size; record elapsed seconds, iterations, CPU, memory, exit status,
   and record byte equality. Repeat across enough distinct trails to report
   p50 and p95, including a timeout/invalid-seed control.
2. Run a bounded load with distinct reserved run IDs and sealed batches at
   2× the planned contributor arrival rate. Measure S3 ACKs, audit and index
   queue age, RDS writes/locks/CPU/IOPS, collision outbox, dead-letter counts,
   ECS scaling, and cost over the same interval. Stop arrivals and show that
   both queues drain to baseline, with every acknowledged object accounted for.
3. Simulate one SQS send failure and one task restart; show outbox repair,
   idempotent replay/indexing, and no lost cursor or duplicate accepted key.

A pass establishes capacity only up to the tested fleet and service limits.
It does not guarantee that every unsampled point is valid or that a collision
will occur by any deadline. A failure to drain leads to a measured lower
admission ceiling, more verifier capacity, or a faster exact verifier before
raising the public cap. An invalid-seed control that reaches the candidate
index without quarantine blocks release until the integrity path is fixed.

Related prior design and obstruction: `aburan28/cryptanalysis`,
`ecc2k130/runner/public/ADMISSION-AND-SCALE.md` and
`problems/ecc2k130-public-gateway-activation.md`. The exact service code and
current tests are in this repository's `src/`, `verifier/`, `sql/`, and
`tests/` paths.
