# DP32 verifier and ingestion capacity

Status: open. Scope: the `ecc-challenges.com` public `ecc2k-130` admission
gateway at distinguished point (DP) weight 32. It uses the pinned sigma walk,
1 MiB batches, a 1/128 audit selection for each batch after the first (made
after sealing) and the dedicated private `rho_dp` instance.

## Specific obstruction

The exact host replay binary is built and wired to a separate SQS audit pool.
Two numbers are still unmeasured on release infrastructure: the DP32 replay
time per selected seed, and the sustained audit and index drain rate. The CPU
walk can take hundreds of millions of steps per report. Without a measured
service time and a production-shaped arrival and drain test, the configured
ceilings of 32 audit tasks and eight index tasks cannot justify opening to an
unbounded global fleet. A growing queue would eventually trigger admission
backpressure and local worker spooling. The service would stay durable but
would not keep up with the offered load.

This is a resource and validation gap, not a proof that the design cannot
scale. The original campaign already has DPs in its own RDS instance, but
their count says nothing about this verifier or the public gateway's
throughput. Historical matching stays unavailable until the separate import
and backmatch; until then the public candidate index can match new points to
new points only. The reserved public run range has 15,535 IDs, which also
bounds the number of distinct public run identities unless the registry is
audited or the format changes. The original campaign's RDS ingress is a
separate security exposure outside the public platform and does not establish
a throughput result.

## What would clear it

1. Capture a frozen DP32 seed and record pair from the same sigma campaign,
   with exact source and image digests. Replay it independently on the release
   audit task size. Record elapsed seconds, iterations, CPU, memory, exit
   status and record byte equality. Repeat across enough distinct trails to
   report p50 and p95, and include a timeout control and an invalid-seed
   control.
2. Run a bounded load with distinct reserved run IDs and sealed batches at 2×
   the planned arrival rate. Over the same interval, measure S3
   acknowledgments, audit and index queue age, RDS writes, locks, CPU and IOPS,
   the collision outbox, dead-letter counts, ECS scaling and cost. Stop
   arrivals and show that both queues drain to baseline with every acknowledged
   object accounted for.
3. Simulate one SQS send failure and one task restart. Show outbox repair,
   idempotent replay and indexing, and no lost cursor or duplicate accepted
   key.

A pass establishes capacity only up to the tested fleet and service limits.
It does not show that every unsampled point is valid or that a collision will
occur by any deadline. If the queues fail to drain, the fix is a lower
measured admission ceiling, more verifier capacity or a faster exact verifier,
before the public cap is raised. An invalid-seed control that reaches the
candidate index without quarantine blocks release until the integrity path is
fixed.

Earlier design and obstruction notes are in `aburan28/cryptanalysis`:
`ecc2k130/runner/public/ADMISSION-AND-SCALE.md` and
`problems/ecc2k130-public-gateway-activation.md`. The service code and current
tests are in this repository's `src/`, `verifier/`, `sql/` and `tests/`
directories.
