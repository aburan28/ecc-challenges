"""Recoverable v2 verification, immutable receipts, and bounded point indexing.

All records in a batch pass algebraic verification before any point from that
batch reaches the global index. Client trail lengths are never treated as
verified work.

The SQS caller should retain a message when process() returns "not_ready".
Other returned states are terminal for that delivery. Exceptions are retryable;
the metadata outbox also repairs their delivery.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from uuid import UUID, uuid4

from .codec import MAX_BATCH_BYTES, Record, canonical, namespace
from .index import collision_lead_id
from .service import Service

LOG = logging.getLogger(__name__)

VERIFIER_ID = "certicom-affine-algebraic-v2"
LEASE_MINUTES = 20
CHECKPOINT_RECORDS = 4096
CHECKPOINT_SECONDS = 20
INDEX_THREADS = 16
HEX = re.compile(r"^[0-9a-f]{64}$")


class LostLease(RuntimeError):
    """Another delivery owns the batch, or this delivery's lease expired."""


class InvalidBatch(ValueError):
    """A canonical record or its algebraic coefficient witness is invalid."""


def _verifier_sha256() -> str:
    """Bind resumed work to the actual arithmetic, codec, registry, and driver."""
    root = Path(__file__).parent
    result = hashlib.sha256()
    for name in (
        "codec.py",
        "curves.py",
        "instances.py",
        "instances.json",
        "pipeline.py",
    ):
        result.update(name.encode("ascii"))
        result.update(b"\x00")
        result.update((root / name).read_bytes())
        result.update(b"\x00")
    return result.hexdigest()


class Pipeline:
    def __init__(self, legacy, db, archive, settings, point_index):
        self.legacy = legacy
        self.db = db
        self.archive = archive
        self.settings = settings
        self.point_index = point_index
        self.service = Service(legacy, db, archive, settings)
        self.verifier_sha256 = _verifier_sha256()
        self._heartbeat_at = {}

    @staticmethod
    def _source(batch: dict) -> dict:
        return {
            "batch_id": str(batch["id"]),
            "namespace_id": batch["namespace_id"],
            "run_id": str(batch["run_id"]),
            "object_key": batch["object_key"],
            "version_id": batch["version_id"],
            "sha256": batch["sha256"],
            "manifest_key": batch["manifest_key"],
            "admission_key": batch["admission_key"],
            "byte_offset": batch["byte_offset"],
            "byte_length": batch["byte_length"],
            "record_bytes": batch["record_bytes"],
            "records": batch["records"],
        }

    def _claim(self, batch_id: UUID, token: UUID) -> dict | None:
        with self.db.connection() as conn:
            return conn.execute(
                """
                UPDATE ecc_public.dp_batches AS b
                SET processing_token=%s,
                    verification_lease_until=clock_timestamp()
                        + make_interval(mins => %s),
                    verification_started_at=coalesce(
                        verification_started_at,clock_timestamp()),
                    progress_at=clock_timestamp(),
                    attempts=attempts+1,
                    state=CASE
                        WHEN verifier_id=%s AND verifier_sha256=%s
                        THEN state ELSE 'admitted' END,
                    verified_records=CASE
                        WHEN verifier_id=%s AND verifier_sha256=%s
                        THEN verified_records ELSE 0 END,
                    validation_progress_key=CASE
                        WHEN verifier_id=%s AND verifier_sha256=%s
                        THEN validation_progress_key ELSE NULL END,
                    verified_key=CASE
                        WHEN verifier_id=%s AND verifier_sha256=%s
                        THEN verified_key ELSE NULL END,
                    verifier_id=%s,
                    verifier_sha256=%s,
                    error=NULL
                FROM ecc_public.dp_namespaces AS n
                WHERE b.id=%s AND b.namespace_id=n.id
                  AND b.state IN ('admitted','verified')
                  AND b.admission_key IS NOT NULL
                  AND b.pressure_released_at IS NULL
                  AND (b.verification_lease_until IS NULL
                       OR b.verification_lease_until<=clock_timestamp())
                RETURNING b.*,n.campaign_id,n.manifest AS namespace_manifest
                """,
                (
                    token, LEASE_MINUTES,
                    VERIFIER_ID, self.verifier_sha256,
                    VERIFIER_ID, self.verifier_sha256,
                    VERIFIER_ID, self.verifier_sha256,
                    VERIFIER_ID, self.verifier_sha256,
                    VERIFIER_ID, self.verifier_sha256,
                    batch_id,
                ),
            ).fetchone()

    def _unclaimed_state(self, batch_id: UUID) -> str:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT state FROM ecc_public.dp_batches WHERE id=%s",
                (batch_id,),
            ).fetchone()
        if not row:
            return "missing"
        if row["state"] in ("indexed", "failed", "expired"):
            return row["state"]
        return "not_ready"

    def _touch(self, batch: dict, token: UUID, *, force: bool = False) -> None:
        if not force and time.monotonic() < self._heartbeat_at.get(token, 0):
            return
        with self.db.connection() as conn:
            row = conn.execute(
                """
                UPDATE ecc_public.dp_batches
                SET progress_at=clock_timestamp(),
                    verification_lease_until=clock_timestamp()
                        + make_interval(mins => %s)
                WHERE id=%s AND processing_token=%s
                  AND state IN ('admitted','verified')
                  AND verification_lease_until>clock_timestamp()
                RETURNING id
                """,
                (LEASE_MINUTES, batch["id"], token),
            ).fetchone()
        if not row:
            raise LostLease("batch verification lease is unavailable")
        self._heartbeat_at[token] = time.monotonic() + 20

    def _read_receipt(
        self, key: str, kind: str, namespace_id: str, identity: str
    ) -> dict:
        prefix = f"{self.archive.prefix}/{kind}/{namespace_id}/{identity}/"
        if not isinstance(key, str) or not key.startswith(prefix):
            raise RuntimeError("receipt is bound to another namespace or identity")
        name = key[len(prefix):]
        if not name.endswith(".json") or not HEX.fullmatch(name[:-5]):
            raise RuntimeError("invalid content-addressed receipt key")
        raw = self.archive.storage.get(key)
        if len(raw) > 4 * MAX_BATCH_BYTES:
            raise RuntimeError("receipt exceeds its bounded size")
        if hashlib.sha256(raw).hexdigest() != name[:-5]:
            raise RuntimeError("receipt content differs from its content address")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise RuntimeError("receipt is not valid JSON") from exc
        if type(value) is not dict or canonical(value) != raw:
            raise RuntimeError("receipt is not a canonical JSON object")
        return value

    def _namespace(self, batch: dict):
        ns = namespace(batch["campaign_id"])
        if (
            ns.id != batch["namespace_id"]
            or ns.record_bytes != batch["record_bytes"]
            or canonical(ns.public()) != canonical(batch["namespace_manifest"])
        ):
            raise RuntimeError("batch namespace differs from the pinned registry")
        raw = self.archive.storage.get(self.archive.namespace_key(ns.id))
        if raw != canonical(ns.public()):
            raise RuntimeError("archived namespace differs from the pinned registry")
        return ns

    def _validate_envelope(
        self, batch: dict, ns, *, require_admission: bool = True
    ) -> None:
        manifest = self._read_receipt(
            batch["manifest_key"], "batches", ns.id, str(batch["id"])
        )
        expected = {
            "protocol": 2,
            "kind": "batch",
            "batch_id": str(batch["id"]),
            "namespace_id": ns.id,
            "run_id": str(batch["run_id"]),
            "offset": batch["byte_offset"],
            "record_bytes": ns.record_bytes,
            "byte_length": batch["byte_length"],
            "records": batch["records"],
            "sha256": batch["sha256"],
            "object_key": batch["object_key"],
            "version_id": batch["version_id"],
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise RuntimeError("batch manifest differs from admitted metadata")
        expected_raw_key = self.archive.raw_key(
            ns.id, str(batch["run_id"]), batch["byte_offset"], batch["sha256"]
        )
        if batch["object_key"] != expected_raw_key:
            raise RuntimeError("raw object key differs from its admitted identity")
        if require_admission:
            receipt = self._read_receipt(
                batch["admission_key"], "admissions", ns.id, str(batch["id"])
            )
            if canonical(receipt) != canonical(Service.admission_receipt(batch)):
                raise RuntimeError("admission receipt differs from admitted metadata")

    def _raw(self, batch: dict) -> bytes:
        if not 0 < batch["byte_length"] <= MAX_BATCH_BYTES:
            raise RuntimeError("admitted batch exceeds the raw byte bound")
        obj = self.archive.s3.get_object(
            Bucket=self.archive.bucket,
            Key=batch["object_key"],
            VersionId=batch["version_id"],
        )
        body = obj["Body"]
        try:
            if obj.get("ContentLength") != batch["byte_length"]:
                raise RuntimeError("pinned raw object length differs from admission")
            if obj.get("VersionId") != batch["version_id"]:
                raise RuntimeError("archive returned another raw object version")
            raw = body.read(MAX_BATCH_BYTES + 1)
        finally:
            body.close()
        if len(raw) != batch["byte_length"]:
            raise RuntimeError("pinned raw object is truncated or oversized")
        if hashlib.sha256(raw).hexdigest() != batch["sha256"]:
            raise RuntimeError("pinned raw object SHA-256 differs from admission")
        return raw

    def _resume_count(self, batch: dict, ns, raw: bytes) -> int:
        count = batch["verified_records"]
        if not count:
            return 0
        key = batch["validation_progress_key"]
        if not key:
            return 0
        try:
            receipt = self._read_receipt(
                key, "checkpoints", ns.id, str(batch["id"])
            )
            expected = {
                "protocol": 2,
                "kind": "validation-progress",
                "source": self._source(batch),
                "verifier_id": VERIFIER_ID,
                "verifier_sha256": self.verifier_sha256,
                "checked_records": count,
                "checked_prefix_sha256": hashlib.sha256(
                    raw[:count * ns.record_bytes]
                ).hexdigest(),
            }
            if (
                type(count) is not int
                or not 0 <= count <= batch["records"]
                or canonical(receipt) != canonical(expected)
            ):
                raise RuntimeError("validation progress binding differs")
            return count
        except (RuntimeError, ValueError, TypeError):
            # Preserve the old receipt and replay the prefix. A DB counter alone
            # never authorizes skipping algebraic verification.
            LOG.warning("replaying unbound v2 validation prefix for %s", batch["id"])
            return 0

    def _checkpoint(
        self, batch: dict, ns, token: UUID, count: int, prefix_sha256: str
    ) -> None:
        receipt = {
            "protocol": 2,
            "kind": "validation-progress",
            "source": self._source(batch),
            "verifier_id": VERIFIER_ID,
            "verifier_sha256": self.verifier_sha256,
            "checked_records": count,
            "checked_prefix_sha256": prefix_sha256,
        }
        key = self.archive.put_receipt(
            ns.id, "checkpoints", str(batch["id"]), receipt
        )
        with self.db.connection() as conn:
            changed = conn.execute(
                """
                UPDATE ecc_public.dp_batches
                SET verified_records=%s,validation_progress_key=%s,
                    progress_at=clock_timestamp(),
                    verification_lease_until=clock_timestamp()
                        + make_interval(mins => %s)
                WHERE id=%s AND processing_token=%s AND state='admitted'
                  AND verification_lease_until>clock_timestamp()
                RETURNING id
                """,
                (count, key, LEASE_MINUTES, batch["id"], token),
            ).fetchone()
        if not changed:
            raise LostLease("batch checkpoint lost its verification lease")

    def _verified_receipt(self, batch: dict) -> dict:
        return {
            "protocol": 2,
            "kind": "batch-verification",
            "state": "verified",
            "source": self._source(batch),
            "verifier_id": VERIFIER_ID,
            "verifier_sha256": self.verifier_sha256,
            "verified_records": batch["records"],
            "checks": [
                "version-pinned raw SHA-256 and length",
                "namespace codec canonicality",
                "finite affine point on assigned curve",
                "namespace distinguished-point predicate",
                "canonical a and b with point = aG + bQ",
            ],
            "trail_steps_verified": False,
        }

    def _verify(self, batch: dict, ns, token: UUID, raw: bytes) -> list[Record]:
        try:
            records = ns.decode(raw)
        except ValueError as exc:
            raise InvalidBatch("batch contains a noncanonical DP record") from exc
        if len(records) != batch["records"]:
            raise RuntimeError("decoded record count differs from admission")

        if batch["state"] == "verified":
            receipt = self._read_receipt(
                batch["verified_key"], "verified", ns.id, str(batch["id"])
            )
            if canonical(receipt) != canonical(self._verified_receipt(batch)):
                raise RuntimeError("verified receipt differs from its source binding")
            self._touch(batch, token)
            return records

        resume = self._resume_count(batch, ns, raw)
        prefix = hashlib.sha256(raw[:resume * ns.record_bytes])
        last_checkpoint = time.monotonic()
        since_checkpoint = 0
        checked_witnesses = {(ns.point_key(r), ns.witness(r)) for r in records[:resume]}
        for index in range(resume, len(records)):
            try:
                witness = (ns.point_key(records[index]), ns.witness(records[index]))
                if witness not in checked_witnesses:
                    ns.verify(records[index])
                    checked_witnesses.add(witness)
            except ValueError as exc:
                raise InvalidBatch(
                    f"DP record {index} fails canonical algebraic verification"
                ) from exc
            start = index * ns.record_bytes
            prefix.update(raw[start:start + ns.record_bytes])
            since_checkpoint += 1
            if (
                since_checkpoint >= CHECKPOINT_RECORDS
                or time.monotonic() - last_checkpoint >= CHECKPOINT_SECONDS
            ):
                self._checkpoint(
                    batch, ns, token, index + 1, prefix.copy().hexdigest()
                )
                last_checkpoint = time.monotonic()
                since_checkpoint = 0

        self._touch(batch, token)
        verified_key = self.archive.put_receipt(
            ns.id, "verified", str(batch["id"]), self._verified_receipt(batch)
        )
        with self.db.connection() as conn:
            changed = conn.execute(
                """
                UPDATE ecc_public.dp_batches
                SET state='verified',verified_key=%s,verified_records=records,
                    verified_counted_at=coalesce(verified_counted_at,clock_timestamp()),
                    progress_at=clock_timestamp(),
                    verification_lease_until=clock_timestamp()
                        + make_interval(mins => %s)
                WHERE id=%s AND processing_token=%s AND state='admitted'
                  AND verification_lease_until>clock_timestamp()
                RETURNING id
                """,
                (verified_key, LEASE_MINUTES, batch["id"], token),
            ).fetchone()
            if changed and batch["verified_counted_at"] is None:
                Service._increment_stats(conn, batch, verified=True)
        if not changed:
            raise LostLease("verified batch lost its verification lease")
        batch["state"] = "verified"
        batch["verified_key"] = verified_key
        batch["verified_records"] = len(records)
        return records

    def _fail(self, batch: dict, ns, token: UUID, message: str) -> None:
        message = message[:500]
        key = self.archive.put_receipt(
            ns.id,
            "verified",
            str(batch["id"]),
            {
                "protocol": 2,
                "kind": "batch-rejection",
                "state": "failed",
                "source": self._source(batch),
                "verifier_id": VERIFIER_ID,
                "verifier_sha256": self.verifier_sha256,
                "error": message,
            },
        )
        with self.db.connection() as conn:
            changed = conn.execute(
                """
                UPDATE ecc_public.dp_batches
                SET state='failed',error=%s,verified_key=%s,
                    pressure_released_at=clock_timestamp(),
                    progress_at=clock_timestamp(),
                    processing_token=NULL,verification_lease_until=NULL
                WHERE id=%s AND processing_token=%s AND state='admitted'
                  AND pressure_released_at IS NULL
                  AND verification_lease_until>clock_timestamp()
                RETURNING byte_length,pressure_shard
                """,
                (message, key, batch["id"], token),
            ).fetchone()
            if not changed:
                raise LostLease("rejected batch lost its verification lease")
            Service._release_pressure(conn, changed)
            conn.execute("""
                UPDATE ecc_public.dp_runs SET suspended_at=coalesce(suspended_at,clock_timestamp())
                WHERE id=%s
            """, (batch["run_id"],))

    def _lead(self, batch: dict, ns, record: Record, index: int, prior: dict) -> str | None:
        lead_id = collision_lead_id(ns, record, prior)
        if lead_id is None:
            return None
        point = ns.point_key(record)
        witness = ns.witness(record)
        lead_receipt = {
            "protocol": 2,
            "kind": "collision-lead",
            "lead_id": lead_id,
            "namespace_id": ns.id,
            "point_key": point.hex(),
            "first": {
                "batch_id": prior["batch_id"],
                "record_index": prior["record_index"],
                "witness": bytes(prior["witness"]).hex(),
                "verified_key": prior["verified_key"],
            },
            "second": {
                "batch_id": str(batch["id"]),
                "record_index": index,
                "witness": witness.hex(),
                "verified_key": batch["verified_key"],
            },
        }
        lead_key = self.archive.put_receipt(
            ns.id, "collisions", lead_id, lead_receipt
        )
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO ecc_public.dp_collision_leads
                    (id,namespace_id,point_key,first_batch,first_index,
                     second_batch,second_index,first_witness,second_witness,
                     result_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    lead_id, ns.id, point, UUID(prior["batch_id"]),
                    prior["record_index"], batch["id"], index,
                    bytes(prior["witness"]), witness, lead_key,
                ),
            )
            stored = conn.execute(
                "SELECT * FROM ecc_public.dp_collision_leads WHERE id=%s",
                (lead_id,),
            ).fetchone()
        if (
            not stored
            or stored["namespace_id"] != ns.id
            or bytes(stored["point_key"]) != point
            or sorted((bytes(stored["first_witness"]), bytes(stored["second_witness"])))
            != sorted((bytes(prior["witness"]), witness))
        ):
            raise RuntimeError("collision lead identity differs from its stored binding")
        self._resolve(ns, stored)
        return lead_id

    def _resolve(self, ns, lead: dict) -> str:
        lead_id = lead["id"]
        if lead["state"] != "pending":
            result = self._read_receipt(
                lead["result_key"], "collisions", ns.id, lead_id
            )
            if (
                result.get("kind") != "collision-result"
                or result.get("lead_id") != lead_id
                or result.get("namespace_id") != ns.id
                or result.get("state") != lead["state"]
            ):
                raise RuntimeError("resolved collision receipt differs from metadata")
            if lead["state"] == "verified":
                scalar = result.get("scalar")
                if (
                    not isinstance(scalar, str)
                    or not scalar.startswith("0x")
                    or len(scalar) > 2 + 2 * ns.scalar_bytes
                ):
                    raise RuntimeError("resolved scalar is not canonical")
                value = int(scalar, 16)
                if not 0 <= value < ns.instance.n or hex(value) != scalar:
                    raise RuntimeError("resolved scalar is not canonical")
                if ns.instance.mul(value, ns.instance.G) != ns.instance.Q:
                    raise RuntimeError("resolved scalar fails dG = Q")
            return lead["state"]

        source = self._read_receipt(
            lead["result_key"], "collisions", ns.id, lead_id
        )
        if (
            source.get("kind") != "collision-lead"
            or source.get("lead_id") != lead_id
            or source.get("namespace_id") != ns.id
            or source.get("point_key") != bytes(lead["point_key"]).hex()
        ):
            raise RuntimeError("pending collision receipt differs from metadata")
        for label, batch_column, index_column, witness_column in (
            ("first", "first_batch", "first_index", "first_witness"),
            ("second", "second_batch", "second_index", "second_witness"),
        ):
            location = source.get(label)
            if not isinstance(location, dict) or (
                location.get("batch_id") != str(lead[batch_column])
                or location.get("record_index") != lead[index_column]
                or location.get("witness") != bytes(lead[witness_column]).hex()
            ):
                raise RuntimeError("collision source location differs from metadata")

        raw_point = bytes(lead["point_key"])
        if len(raw_point) != 2 * ns.field_bytes:
            raise RuntimeError("collision point width differs from namespace")
        point = (
            int.from_bytes(raw_point[:ns.field_bytes], "big"),
            int.from_bytes(raw_point[ns.field_bytes:], "big"),
        )
        pairs = []
        for column in ("first_witness", "second_witness"):
            witness = bytes(lead[column])
            if len(witness) != 2 * ns.scalar_bytes:
                raise RuntimeError("collision coefficient width differs from namespace")
            pairs.append(
                (
                    int.from_bytes(witness[:ns.scalar_bytes], "big"),
                    int.from_bytes(witness[ns.scalar_bytes:], "big"),
                )
            )

        state = "rejected"
        reason = "coefficient witness fails independent algebraic verification"
        scalar = None
        try:
            for a, b in pairs:
                ns.verify(Record(0, 1, point, a, b))
            (a1, b1), (a2, b2) = pairs
            denominator = (b2 - b1) % ns.instance.n
            if not denominator:
                state = "degenerate"
                reason = "coefficient difference has zero target denominator"
            else:
                try:
                    candidate = (
                        (a1 - a2) * pow(denominator, -1, ns.instance.n)
                    ) % ns.instance.n
                except ValueError:
                    state = "degenerate"
                    reason = "coefficient difference has a noninvertible denominator"
                else:
                    if ns.instance.mul(candidate, ns.instance.G) == ns.instance.Q:
                        state = "verified"
                        reason = "recovered scalar independently satisfies dG = Q"
                        scalar = hex(candidate)
                    else:
                        reason = "recovered scalar does not satisfy dG = Q"
        except ValueError:
            pass

        receipt = {
            "protocol": 2,
            "kind": "collision-result",
            "lead_id": lead_id,
            "namespace_id": ns.id,
            "point_key": raw_point.hex(),
            "lead_receipt_key": lead["result_key"],
            "first": source["first"],
            "second": source["second"],
            "state": state,
            "reason": reason,
            "scalar": scalar,
            "verifier_id": VERIFIER_ID,
            "verifier_sha256": self.verifier_sha256,
        }
        result_key = self.archive.put_receipt(
            ns.id, "collisions", lead_id, receipt
        )
        with self.db.connection() as conn:
            changed = conn.execute(
                """
                UPDATE ecc_public.dp_collision_leads
                SET state=%s,result_key=%s,resolved_at=clock_timestamp()
                WHERE id=%s AND state='pending'
                RETURNING state
                """,
                (state, result_key, lead_id),
            ).fetchone()
            if not changed:
                current = conn.execute(
                    "SELECT state FROM ecc_public.dp_collision_leads WHERE id=%s",
                    (lead_id,),
                ).fetchone()
                if not current or current["state"] != state:
                    raise RuntimeError("concurrent collision resolution disagrees")
        return state

    def _index(self, batch: dict, ns, token: UUID, records: list[Record]) -> None:
        new_points = 0
        first_indices = {}
        for index, record in enumerate(records):
            first_indices.setdefault((ns.point_key(record), ns.witness(record)), index)
        unique_indices = list(first_indices.values())
        duplicates = len(records) - len(unique_indices)
        leads = set()
        with ThreadPoolExecutor(max_workers=INDEX_THREADS) as executor:
            for start in range(0, len(unique_indices), INDEX_THREADS):
                self._touch(batch, token)
                tasks = [
                    (
                        index,
                        executor.submit(
                            self.point_index.put,
                            ns, records[index], batch["id"], index,
                            batch["verified_key"],
                        ),
                    )
                    for index in unique_indices[start:start + INDEX_THREADS]
                ]
                for index, future in tasks:
                    prior = future.result()
                    if prior is None:
                        new_points += 1
                        continue
                    own_first = (
                        prior["batch_id"] == str(batch["id"])
                        and prior["record_index"] == index
                    )
                    if own_first:
                        if bytes(prior["witness"]) != ns.witness(records[index]):
                            raise RuntimeError("retry point owner has another witness")
                        new_points += 1
                    else:
                        duplicates += 1
                    lead_id = self._lead(batch, ns, records[index], index, prior)
                    if lead_id is not None:
                        leads.add(lead_id)
                self._touch(batch, token)

        if new_points + duplicates != len(records):
            raise RuntimeError("index accounting does not cover the verified batch")
        self.archive.put_receipt(
            ns.id,
            "checkpoints",
            str(batch["id"]),
            {
                "protocol": 2,
                "kind": "batch-indexed",
                "source": self._source(batch),
                "verified_key": batch["verified_key"],
                "new_points": new_points,
                "deduplicated_points": duplicates,
                "collision_leads": sorted(leads),
            },
        )
        with self.db.connection() as conn:
            changed = conn.execute(
                """
                UPDATE ecc_public.dp_batches
                SET state='indexed',new_points=%s,deduplicated_points=%s,
                    indexed_at=clock_timestamp(),progress_at=clock_timestamp(),
                    pressure_released_at=clock_timestamp(),
                    processing_token=NULL,verification_lease_until=NULL,error=NULL
                WHERE id=%s AND processing_token=%s AND state='verified'
                  AND verified_key=%s AND verified_records=records
                  AND pressure_released_at IS NULL
                  AND verification_lease_until>clock_timestamp()
                RETURNING byte_length,pressure_shard
                """,
                (
                    new_points, duplicates, batch["id"], token,
                    batch["verified_key"],
                ),
            ).fetchone()
            if not changed:
                raise LostLease("indexed batch lost its verification lease")
            Service._increment_stats(conn, batch, indexed=True,
                new_points=new_points, deduplicated_points=duplicates)
            Service._release_pressure(conn, changed)

    def _retry(self, batch: dict, token: UUID, exc: Exception) -> None:
        # Persist the exception class rather than provider messages or payloads.
        error = f"retryable infrastructure failure: {type(exc).__name__}"[:500]
        with self.db.connection() as conn:
            conn.execute(
                """
                UPDATE ecc_public.dp_batches
                SET processing_token=NULL,verification_lease_until=NULL,
                    queued_at=NULL,error=%s
                WHERE id=%s AND processing_token=%s
                  AND state IN ('admitted','verified')
                """,
                (error, batch["id"], token),
            )

    def process(self, batch_uuid) -> str:
        batch_id = UUID(str(batch_uuid))
        token = uuid4()
        batch = self._claim(batch_id, token)
        if not batch:
            return self._unclaimed_state(batch_id)
        ns = None
        try:
            ns = self._namespace(batch)
            self._validate_envelope(batch, ns)
            raw = self._raw(batch)
            records = self._verify(batch, ns, token, raw)
            self._index(batch, ns, token, records)
            return "indexed"
        except LostLease:
            return "not_ready"
        except InvalidBatch as exc:
            if ns is None:
                raise RuntimeError("invalid batch reported before namespace binding") from exc
            try:
                self._fail(batch, ns, token, str(exc))
            except LostLease:
                return "not_ready"
            return "failed"
        except Exception as exc:
            try:
                self._retry(batch, token, exc)
            except Exception:
                LOG.exception("v2 retry metadata repair deferred for %s", batch_id)
            raise
        finally:
            self._heartbeat_at.pop(token, None)

    def repair(self, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid reconciliation work limit")
        repaired = self.service.expire_intents(limit=limit)
        with self.db.connection() as conn:
            missing = conn.execute(
                """
                SELECT b.*,n.campaign_id,n.manifest AS namespace_manifest
                FROM ecc_public.dp_batches b
                JOIN ecc_public.dp_namespaces n ON n.id=b.namespace_id
                WHERE b.state='admitted' AND b.admission_key IS NULL
                ORDER BY b.created_at,b.id LIMIT %s
                """,
                (limit,),
            ).fetchall()
        for batch in missing:
            try:
                ns = self._namespace(batch)
                self._validate_envelope(batch, ns, require_admission=False)
                key = self.archive.put_receipt(
                    ns.id, "admissions", str(batch["id"]),
                    Service.admission_receipt(batch),
                )
                with self.db.connection() as conn:
                    changed = conn.execute(
                        """
                        UPDATE ecc_public.dp_batches SET admission_key=%s
                        WHERE id=%s AND state='admitted' AND manifest_key=%s
                          AND (admission_key IS NULL OR admission_key=%s)
                        RETURNING id
                        """,
                        (key, batch["id"], batch["manifest_key"], key),
                    ).fetchone()
                repaired += bool(changed)
            except Exception:
                LOG.exception("v2 admission repair deferred for %s", batch["id"])

        with self.db.connection() as conn:
            stale = conn.execute(
                """
                SELECT id FROM ecc_public.dp_batches
                WHERE state IN ('admitted','verified')
                  AND admission_key IS NOT NULL
                  AND pressure_released_at IS NULL
                  AND (verification_lease_until IS NULL
                       OR verification_lease_until<=clock_timestamp())
                  AND (
                    queued_at IS NULL
                    OR (
                      queued_at<clock_timestamp()-interval '30 minutes'
                      AND coalesce(progress_at,created_at)
                          <clock_timestamp()-interval '30 minutes'
                    )
                  )
                ORDER BY created_at,id LIMIT %s
                """,
                (limit,),
            ).fetchall()
        for row in stale:
            try:
                self.archive.storage.sqs.send_message(
                    QueueUrl=self.settings.audit_queue_url,
                    MessageBody=json.dumps({"v2_batch_id": str(row["id"])}),
                )
                with self.db.connection() as conn:
                    conn.execute(
                        """
                        UPDATE ecc_public.dp_batches
                        SET queued_at=clock_timestamp()
                        WHERE id=%s AND state IN ('admitted','verified')
                          AND admission_key IS NOT NULL
                          AND (verification_lease_until IS NULL
                               OR verification_lease_until<=clock_timestamp())
                        """,
                        (row["id"],),
                    )
                repaired += 1
            except Exception:
                LOG.exception("v2 queue repair deferred for %s", row["id"])
        return repaired
