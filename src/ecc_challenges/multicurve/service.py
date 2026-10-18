"""Bounded admission of immutable, version-pinned v2 distinguished points."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from ..service import Rejected
from .codec import MAX_BATCH_BYTES, canonical, namespace, pinned_namespace

LOG = logging.getLogger(__name__)
HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_CURSOR = (1 << 63) - 1 - MAX_BATCH_BYTES


def uuid_value(value, name: str) -> UUID:
    if not isinstance(value, (str, UUID)):
        raise Rejected(400, f"{name} must be a UUID")
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise Rejected(400, f"{name} must be a UUID") from None


def integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise Rejected(400, f"invalid {name}")
    return value


class Service:
    def __init__(self, legacy, db, archive, settings):
        self.legacy = legacy
        self.db = db
        self.archive = archive
        self.settings = settings

    @staticmethod
    def _namespace(campaign: str):
        if not isinstance(campaign, str) or len(campaign) > 80:
            raise Rejected(400, "invalid campaign")
        try:
            return namespace(campaign)
        except (KeyError, ValueError):
            raise Rejected(404, "unknown campaign") from None

    def config(self, campaign: str) -> dict:
        return self._namespace(campaign).public()

    def _run(self, conn, run_id: UUID, key: dict, *, lock=False, allow_closed=False) -> dict:
        suffix = " FOR UPDATE OF r" if lock else ""
        row = conn.execute("""
            SELECT r.*,n.campaign_id,n.record_bytes,n.manifest
            FROM ecc_public.dp_runs r
            JOIN ecc_public.dp_namespaces n ON n.id=r.namespace_id
            JOIN ecc_public.api_keys k ON k.id=r.key_id
            JOIN ecc_public.accounts a ON a.github_id=r.account_id
            WHERE r.id=%s AND r.account_id=%s AND r.key_id=%s
              AND r.suspended_at IS NULL AND k.revoked_at IS NULL
              AND a.suspended_at IS NULL
        """ + suffix, (run_id, key["account_id"], key["id"])).fetchone()
        if not row:
            raise Rejected(401, "run or key is unavailable")
        if row["closed_at"] is not None and not allow_closed:
            raise Rejected(409, "run is closed; use a new state directory to register another run")
        try:
            ns = pinned_namespace(row["campaign_id"], row["manifest"])
        except (KeyError, ValueError):
            raise Rejected(503, "assigned namespace is unavailable") from None
        if (ns.id != row["namespace_id"] or ns.record_bytes != row["record_bytes"]
                or canonical(ns.public()) != canonical(row["manifest"])):
            raise Rejected(503, "assigned namespace is unavailable")
        return row

    @staticmethod
    def _registration(run: dict) -> dict:
        return {
            "protocol": 2, "run_id": str(run["id"]),
            "namespace_id": run["namespace_id"], "campaign": run["campaign_id"],
            "record_bytes": run["record_bytes"], "cursor": run["cursor"],
            "total_records": run["total_records"], "config": run["manifest"],
            "closed_at": run["closed_at"].isoformat() if run["closed_at"] else None,
        }

    def register(self, key: dict, campaign: str, registration_id: str) -> dict:
        ns = self._namespace(campaign)
        campaign = ns.campaign
        nonce = uuid_value(registration_id, "registration_id")
        with self.db.connection() as conn:
            account = conn.execute("""
                SELECT suspended_at FROM ecc_public.accounts
                WHERE github_id=%s FOR UPDATE
            """, (key["account_id"],)).fetchone()
            if not account or account["suspended_at"]:
                raise Rejected(401, "account is unavailable")
            prior = conn.execute("""
                SELECT id,namespace_id FROM ecc_public.dp_runs
                WHERE key_id=%s AND registration_id=%s
            """, (key["id"], nonce)).fetchone()
            if prior:
                if prior["namespace_id"] != ns.id:
                    raise Rejected(409, "registration_id belongs to another namespace")
                return self._registration(self._run(conn, prior["id"], key))
            old = conn.execute("""
                SELECT 1 FROM ecc_public.runs
                WHERE key_id=%s AND registration_id=%s
            """, (key["id"], nonce)).fetchone()
            if old:
                raise Rejected(409, "registration_id belongs to the legacy protocol")
            counts = conn.execute("""
                SELECT count(*) AS lifetime,
                       count(*) FILTER (WHERE suspended_at IS NULL AND closed_at IS NULL) AS active
                FROM (
                    SELECT suspended_at,NULL::timestamptz AS closed_at
                    FROM ecc_public.runs WHERE account_id=%s
                    UNION ALL
                    SELECT suspended_at,closed_at FROM ecc_public.dp_runs WHERE account_id=%s
                ) AS all_runs
            """, (key["account_id"], key["account_id"])).fetchone()
            if counts["lifetime"] >= self.settings.max_account_runs_lifetime:
                raise Rejected(429, "account lifetime run limit reached")
            if counts["active"] >= 8:
                raise Rejected(429, "account run limit reached")
            # Archive the exact public manifest before a run can reference it.
            self.archive.ensure_namespace(ns)
            conn.execute("""
                INSERT INTO ecc_public.dp_namespaces(id,campaign_id,manifest,record_bytes)
                VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING
            """, (ns.id, campaign, Jsonb(ns.public()), ns.record_bytes))
            stored = conn.execute("""
                SELECT campaign_id,record_bytes,manifest
                FROM ecc_public.dp_namespaces WHERE id=%s
            """, (ns.id,)).fetchone()
            if (not stored or stored["campaign_id"] != campaign
                    or stored["record_bytes"] != ns.record_bytes
                    or canonical(stored["manifest"]) != canonical(ns.public())):
                raise Rejected(503, "namespace manifest conflict")
            run_id = uuid4()
            conn.execute("""
                INSERT INTO ecc_public.dp_runs
                    (id,namespace_id,account_id,key_id,registration_id)
                VALUES (%s,%s,%s,%s,%s)
            """, (run_id, ns.id, key["account_id"], key["id"], nonce))
            return self._registration(self._run(conn, run_id, key))

    @staticmethod
    def _increment_stats(conn, batch: dict, *, admitted=False, verified=False,
                         indexed=False, new_points=0, deduplicated_points=0) -> None:
        """Call only inside the transaction that wins the guarded transition."""
        # Initialize a zero row before incrementing. A delta-only INSERT would
        # violate cross-phase CHECK constraints before ON CONFLICT could run.
        conn.execute("""
            INSERT INTO ecc_public.dp_namespace_stats(namespace_id,shard)
            VALUES (%s,%s) ON CONFLICT(namespace_id,shard) DO NOTHING
        """, (batch["namespace_id"], batch["pressure_shard"]))
        conn.execute("""
            UPDATE ecc_public.dp_namespace_stats SET
              admitted_records=admitted_records+%s,
              admitted_bytes=admitted_bytes+%s,
              verified_records=verified_records+%s,
              indexed_records=indexed_records+%s,
              unique_points=unique_points+%s,
              deduplicated_points=deduplicated_points+%s
            WHERE namespace_id=%s AND shard=%s
        """, (batch["records"] if admitted else 0,
              batch["byte_length"] if admitted else 0,
              batch["records"] if verified else 0,
              batch["records"] if indexed else 0,
              new_points, deduplicated_points,
              batch["namespace_id"], batch["pressure_shard"]))

    @staticmethod
    def _release_pressure(conn, row: dict) -> None:
        changed = conn.execute("""
            UPDATE ecc_public.dp_pressure
            SET pending_batches=pending_batches-1,pending_bytes=pending_bytes-%s
            WHERE shard=%s AND pending_batches>=1 AND pending_bytes>=%s
            RETURNING shard
        """, (row["byte_length"], row["pressure_shard"], row["byte_length"])).fetchone()
        if not changed:
            raise RuntimeError("v2 pressure reservation is inconsistent")

    def _expire_row(self, conn, batch_id: UUID) -> bool:
        row = conn.execute("""
            UPDATE ecc_public.dp_batches
            SET state='expired',pressure_released_at=clock_timestamp()
            WHERE id=%s AND state='reserved' AND expires_at<=clock_timestamp()
              AND pressure_released_at IS NULL
            RETURNING byte_length,pressure_shard
        """, (batch_id,)).fetchone()
        if not row:
            return False
        self._release_pressure(conn, row)
        return True

    def expire_intents(self, limit: int = 100, *, account_id=None) -> int:
        """Reconciler entry point; never removes uploaded objects."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid expiry work limit")
        condition = " AND r.account_id=%s" if account_id is not None else ""
        params = (account_id, limit) if account_id is not None else (limit,)
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT b.id,b.pressure_shard FROM ecc_public.dp_batches b
                JOIN ecc_public.dp_runs r ON r.id=b.run_id
                WHERE b.state='reserved' AND b.expires_at<=clock_timestamp()
            """ + condition + """
                ORDER BY b.id LIMIT %s FOR UPDATE OF b SKIP LOCKED
            """, params).fetchall()
            conn.execute("""
                SELECT shard FROM ecc_public.dp_pressure
                WHERE shard=ANY(%s) ORDER BY shard FOR UPDATE
            """, (sorted({row["pressure_shard"] for row in rows}),)).fetchall()
            return sum(self._expire_row(conn, row["id"]) for row in rows)

    def _intent_result(self, row: dict) -> dict:
        upload = None
        if row["state"] == "reserved":
            remaining = int((row["expires_at"] - datetime.now(timezone.utc)).total_seconds())
            if remaining < 1:
                raise Rejected(410, "upload intent expired; request another intent")
            upload = self.archive.presign(
                row["object_key"], row["sha256"], row["byte_length"],
                expires=min(900, remaining),
            )
        return {
            "protocol": 2, "batch_id": str(row["id"]),
            "upload_intent_id": str(row["id"]), "run_id": str(row["run_id"]),
            "namespace_id": row["namespace_id"], "offset": row["byte_offset"],
            "byte_length": row["byte_length"], "records": row["records"],
            "sha256": row["sha256"], "expires_at": row["expires_at"].isoformat(),
            "state": row["state"], "upload": upload,
        }

    def intent(self, key: dict, run_id: str, offset: int, byte_length: int,
               records: int, sha256: str) -> dict:
        run_id = uuid_value(run_id, "run_id")
        offset = integer(offset, "offset", 0, MAX_CURSOR)
        byte_length = integer(byte_length, "byte_length", 1, MAX_BATCH_BYTES)
        records = integer(records, "records", 1, MAX_BATCH_BYTES // 32)
        if not isinstance(sha256, str) or not HEX.fullmatch(sha256):
            raise Rejected(400, "sha256 must be a lowercase SHA-256 digest")
        # Expiry commits independently, including when later admission is refused.
        self.expire_intents(limit=8, account_id=key["account_id"])
        with self.db.connection() as conn:
            conn.execute("""
                SELECT github_id FROM ecc_public.accounts
                WHERE github_id=%s FOR UPDATE
            """, (key["account_id"],)).fetchone()
            run = self._run(conn, run_id, key, lock=True)
            if offset % run["record_bytes"] or byte_length != records * run["record_bytes"]:
                raise Rejected(400, "offset, size, and count must align to namespace records")
            prior = conn.execute("""
                SELECT * FROM ecc_public.dp_batches
                WHERE run_id=%s AND byte_offset=%s AND state<>'expired'
            """, (run_id, offset)).fetchone()
            if prior:
                if (prior["sha256"] != sha256 or prior["byte_length"] != byte_length
                        or prior["records"] != records):
                    raise Rejected(409, "offset already holds a different batch")
                if prior["state"] == "failed":
                    raise Rejected(409, "batch failed independent verification")
                result = prior
            else:
                if offset != run["cursor"]:
                    raise Rejected(409, "offset does not equal server cursor")
                outstanding = conn.execute("""
                    SELECT count(*) AS n FROM ecc_public.dp_batches b
                    JOIN ecc_public.dp_runs r ON r.id=b.run_id
                    WHERE r.account_id=%s AND b.state='reserved'
                """, (key["account_id"],)).fetchone()["n"]
                if outstanding >= 8:
                    raise Rejected(429, "account upload intent limit reached", 60)
                pending = conn.execute("""
                    SELECT count(*) AS n FROM ecc_public.dp_batches b
                    JOIN ecc_public.dp_runs r ON r.id=b.run_id
                    WHERE r.account_id=%s AND b.state IN ('reserved','admitted','verified')
                """, (key["account_id"],)).fetchone()["n"]
                if pending >= 32:
                    raise Rejected(429, "account verification backlog limit reached", 60)
                shard = run_id.int % 16
                base, extra = divmod(self.settings.max_pending_batches, 16)
                shard_limit = base + (shard < extra)
                pressure = conn.execute("""
                    UPDATE ecc_public.dp_pressure
                    SET pending_batches=pending_batches+1,
                        pending_bytes=pending_bytes+%s
                    WHERE shard=%s AND pending_batches<%s RETURNING shard
                """, (byte_length, shard, shard_limit)).fetchone()
                if not pressure:
                    raise Rejected(503, "verification backlog is full; pause and retry", 60)
                self.legacy._charge_daily_bytes(conn, key, byte_length)
                batch_id = uuid4()
                object_key = self.archive.raw_key(run["namespace_id"], str(run_id), offset, sha256)
                result = conn.execute("""
                    INSERT INTO ecc_public.dp_batches
                        (id,run_id,namespace_id,record_bytes,byte_offset,byte_length,
                         records,sha256,object_key,pressure_shard)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                """, (batch_id, run_id, run["namespace_id"], run["record_bytes"],
                      offset, byte_length, records, sha256, object_key, shard)).fetchone()
        return self._intent_result(result)

    @staticmethod
    def admission_receipt(batch: dict) -> dict:
        """Stable content also usable by the reconciliation worker."""
        return {
            "protocol": 2, "kind": "batch-admission", "batch_id": str(batch["id"]),
            "namespace_id": batch["namespace_id"], "run_id": str(batch["run_id"]),
            "offset": batch["byte_offset"], "byte_length": batch["byte_length"],
            "records": batch["records"], "sha256": batch["sha256"],
            "object_key": batch["object_key"], "version_id": batch["version_id"],
            "manifest_key": batch["manifest_key"],
            "cursor": batch["byte_offset"] + batch["byte_length"],
        }

    def commit(self, key: dict, run_id: str, batch_id: str) -> dict:
        run_id = uuid_value(run_id, "run_id")
        batch_id = uuid_value(batch_id, "batch_id")
        expired = False
        duplicate = False
        with self.db.connection() as conn:
            run = self._run(conn, run_id, key, lock=True, allow_closed=True)
            batch = conn.execute("""
                SELECT * FROM ecc_public.dp_batches
                WHERE id=%s AND run_id=%s AND namespace_id=%s FOR UPDATE
            """, (batch_id, run_id, run["namespace_id"])).fetchone()
            if not batch:
                raise Rejected(404, "upload intent not found")
            if batch["state"] == "expired":
                raise Rejected(410, "upload intent expired")
            if batch["state"] == "failed":
                raise Rejected(409, "batch failed independent verification")
            if batch["state"] == "reserved":
                if run["closed_at"] is not None:
                    raise Rejected(409, "run is closed")
                if self._expire_row(conn, batch_id):
                    expired = True
                else:
                    if batch["byte_offset"] != run["cursor"]:
                        raise Rejected(409, "offset does not equal server cursor")
                    try:
                        version = self.archive.inspect(
                            batch["object_key"], batch["sha256"], batch["byte_length"]
                        )
                    except ValueError as exc:
                        raise Rejected(409, str(exc)) from exc
                    except Exception as exc:
                        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                        if code in ("404", "NoSuchKey", "NotFound"):
                            raise Rejected(409, "upload is not complete") from exc
                        raise
                    manifest = {
                        "protocol": 2, "kind": "batch", "batch_id": str(batch_id),
                        "namespace_id": batch["namespace_id"], "run_id": str(run_id),
                        "offset": batch["byte_offset"], "record_bytes": batch["record_bytes"],
                        "byte_length": batch["byte_length"], "records": batch["records"],
                        "sha256": batch["sha256"], "object_key": batch["object_key"],
                        "version_id": version, "created_at": batch["created_at"].isoformat(),
                    }
                    manifest_key = self.archive.put_receipt(
                        batch["namespace_id"], "batches", str(batch_id), manifest
                    )
                    admitted = conn.execute("""
                        UPDATE ecc_public.dp_batches
                        SET state='admitted',version_id=%s,manifest_key=%s,
                            admitted_at=clock_timestamp(),progress_at=clock_timestamp()
                        WHERE id=%s AND state='reserved' AND expires_at>clock_timestamp()
                        RETURNING *
                    """, (version, manifest_key, batch_id)).fetchone()
                    if not admitted:
                        if not self._expire_row(conn, batch_id):
                            raise RuntimeError("upload intent changed during admission")
                        expired = True
                    else:
                        batch = admitted
                        self._increment_stats(conn, batch, admitted=True)
                        run = conn.execute("""
                            UPDATE ecc_public.dp_runs
                            SET cursor=cursor+%s,total_bytes=total_bytes+%s,
                                total_records=total_records+%s,last_seen_at=clock_timestamp()
                            WHERE id=%s RETURNING cursor
                        """, (batch["byte_length"], batch["byte_length"],
                              batch["records"], run_id)).fetchone()
            else:
                duplicate = True
        if expired:
            raise Rejected(410, "upload intent expired; request another intent")
        # The DB commit can be repaired after a crash here. No successful response
        # or queue delivery occurs until the immutable admission receipt exists.
        admission_key = batch["admission_key"]
        if not admission_key:
            admission_key = self.archive.put_receipt(
                batch["namespace_id"], "admissions", str(batch_id),
                self.admission_receipt(batch),
            )
            with self.db.connection() as conn:
                changed = conn.execute("""
                    UPDATE ecc_public.dp_batches SET admission_key=%s
                    WHERE id=%s AND manifest_key=%s
                      AND (admission_key IS NULL OR admission_key=%s)
                    RETURNING state
                """, (admission_key, batch_id, batch["manifest_key"], admission_key)).fetchone()
                if not changed:
                    raise RuntimeError("admission receipt metadata conflict")
                batch["state"] = changed["state"]
        if batch["state"] == "failed":
            raise Rejected(409, "batch failed independent verification")
        if batch["state"] == "admitted":
            try:
                self.archive.storage.sqs.send_message(
                    QueueUrl=self.settings.audit_queue_url,
                    MessageBody=json.dumps({"v2_batch_id": str(batch_id)}),
                )
                with self.db.connection() as conn:
                    conn.execute("""
                        UPDATE ecc_public.dp_batches
                        SET queued_at=coalesce(queued_at,clock_timestamp())
                        WHERE id=%s AND admission_key=%s
                    """, (batch_id, admission_key))
            except Exception:
                LOG.exception("v2 batch queue send deferred for %s", batch_id)
        return {
            "ok": True, "batch_id": str(batch_id), "run_id": str(run_id),
            "namespace_id": batch["namespace_id"], "cursor": run["cursor"],
            "batch_end_cursor": batch["byte_offset"] + batch["byte_length"],
            "records": batch["records"], "state": batch["state"],
            "admission_key": admission_key, "duplicate": duplicate,
        }

    def run_status(self, key: dict, run_id: str) -> dict:
        run_id = uuid_value(run_id, "run_id")
        with self.db.connection() as conn:
            run = self._run(conn, run_id, key, allow_closed=True)
            states = conn.execute("""
                SELECT state,count(*) AS batches,coalesce(sum(records),0) AS records
                FROM ecc_public.dp_batches WHERE run_id=%s GROUP BY state
            """, (run_id,)).fetchall()
            pending = conn.execute("""
                SELECT id FROM ecc_public.dp_batches
                WHERE run_id=%s AND byte_offset=%s AND state='reserved'
            """, (run_id, run["cursor"])).fetchone()
            return {
                **self._registration(run), "total_bytes": run["total_bytes"],
                "batches": {row["state"]: {"batches": row["batches"], "records": row["records"]}
                            for row in states},
                "pending_batch_id": str(pending["id"]) if pending else None,
            }

    def close_run(self, key: dict, run_id: str) -> dict:
        """Retire an owned run without discarding an unfinished upload."""
        run_id = uuid_value(run_id, "run_id")
        # Expiry commits independently so a subsequent live reservation refusal
        # cannot roll back the release of unrelated, already expired pressure.
        self.expire_intents(account_id=key["account_id"])
        with self.db.connection() as conn:
            run = self._run(conn, run_id, key, lock=True, allow_closed=True)
            pending = conn.execute("""
                SELECT id FROM ecc_public.dp_batches
                WHERE run_id=%s AND state='reserved' LIMIT 1
            """, (run_id,)).fetchone()
            if pending:
                raise Rejected(409, "finish the pending upload or wait for its expiry before closing")
            row = conn.execute("""
                UPDATE ecc_public.dp_runs
                SET closed_at=coalesce(closed_at,clock_timestamp())
                WHERE id=%s RETURNING closed_at
            """, (run_id,)).fetchone()
            return {"run_id": str(run_id), "namespace_id": run["namespace_id"],
                    "closed_at": row["closed_at"].isoformat()}
