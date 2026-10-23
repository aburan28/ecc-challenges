# Recovery of legacy ECC2K-130 uploads

A terminal replay timeout previously left a batch in `unverified` and removed
its SQS delivery while holding its admission-pressure receipt indefinitely.
After 32 such batches in one shard, the 512-batch deployment limit rejected
every subsequent upload for the run. The reconciler now releases only the
active queue reservation of these terminal batches. Their immutable archives,
audit events, and `unverified` labels remain intact; they are still excluded
from indexing. A later successful audit retry cannot release pressure twice.

`worker/legacy_relay.py` is an operator-installed companion for an existing
legacy run. The operator must enroll the existing identity before starting it;
it does not allocate or rewrite seeds. The current deployment is restricted
to run 249 and reads a mode-0600 API key from its state directory. It retains
hard links to each input-file generation, checks the original record encoding,
and persists the exact pending POST before sending it. A crash or lost reply
retries those same bytes at the same cursor, including while the producer
file is absent during rotation. A durable-journal write failure stops the
relay before it can replace the pending retry bytes. It does not delete the original
worker's files or change its checkpoint format. The relay stops admitting new
source data if the producer changes run ID or truncates a generation; that
condition requires operator review.

The legacy checkpoint format is not accepted by the public checkpoint API.
Preserve its native checkpoint together with the exact binary, campaign
configuration, and preceding point-file bytes in a checksum-addressed recovery
archive. Do not substitute the public worker binary or rewrite the checkpoint
header to make it pass admission. The original upload route can remain active
while the companion catches up.

During the October 21 recovery, the production reconciler was deployed as ECS
revision `ecc-challenges-reconcile:14`, using the existing immutable image and
an embedded, hashed bootstrap applying the tested pressure-release method.
Its bootstrap SHA-256 is
`a57853300181137a9ff492bb8e18658bb8efc9c57de204e0b77328390907d5dc`.
A normal image deployment containing this change replaces that operational
bootstrap. Keep the correction in the image before removing the override.

Validation covers exact acknowledgement retry after a lost response, retention
after source-file unlink, rejection of changed run IDs, preservation of audit
evidence and trust boundaries, admission reopening, and idempotent pressure
release including a later successful audit retry and concurrent suspension.
Suspension decrements only receipts atomically claimed by its own update.

The reconciler also makes the existing 1/128 sampling choice for sealed
trusted-run batches whose audit selection is still unset. This lets ordinary
unsampled batches reach indexing even when all replay workers are occupied by
long checks. First-run audits, existing selections, in-progress replays, and
unverified results retain their original gates.

Indexing performs historical joins before taking the shared run-row lock. It
rechecks trust and suspension immediately before publication and rolls back
all candidate/lead changes if the run was suspended in the meantime. This
prevents slow joins from holding up uploads on the same run. Concurrent tests
exercise both continued upload and suspension during the join interval.
If another transaction holds the run row at final publication, indexing
rolls back and returns `not_ready`, retaining the SQS delivery for retry; it
does not wait while holding batch locks in the reverse order to suspension.

The indexing lock correction is deployed as `ecc-challenges-index:14` with
bootstrap SHA-256
`e115c71c90aace1a47d28da79fe5e5711e52373c2f60df5d4b66616f9a37aa9c`.
Its startup check verifies the deployed baseline method hash before replacing
it. Both operational overrides must be superseded by an image containing the
source fixes, rather than removed independently.

For the public 5090 worker, local checkpoints remain every 60 seconds and
remote checkpoint publication is hourly. A roughly 72 MB checkpoint every
minute would exhaust the shared daily upload allowance. The existing legacy
worker keeps its native checkpoint schedule and receives separate hourly
native recovery archives. Its public relay runs on the pod; a macOS launchd
watchdog checks it and performs the native backups while that host is awake.
