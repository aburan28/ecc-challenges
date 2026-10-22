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
retries those same bytes at the same cursor. It does not delete the original
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
revision `ecc-challenges-reconcile:13`, using the existing immutable image and
an embedded, hashed bootstrap applying the tested pressure-release method.
Its bootstrap SHA-256 is
`ede1758f1f233177684d8298a208633729ccefa55e1d47839b05b6e7c765e907`.
A normal image deployment containing this change replaces that operational
bootstrap. Keep the correction in the image before removing the override.

Validation covers exact acknowledgement retry after a lost response, retention
after source-file unlink, rejection of changed run IDs, preservation of audit
evidence and trust boundaries, admission reopening, and idempotent pressure
release including a later successful audit retry.
