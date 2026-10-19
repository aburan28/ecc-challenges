"""Transactional admission service; S3 is durable source, Postgres is metadata."""

from __future__ import annotations

import hashlib
import hmac
import math
from uuid import UUID

from .protocol import CAMPAIGN, WALK_ID, DP_WEIGHT, DP_WEIGHTS, RECORD_BYTES, check_records, check_checkpoint
from .security import generate_key, parse_and_digest, same_digest


class Rejected(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class Service:
    def __init__(self, db, storage, settings):
        self.db = db
        self.storage = storage
        self.settings = settings

    def upsert_account(self, github_id: int, login: str) -> None:
        if github_id <= 0 or not login or len(login) > 80:
            raise Rejected(400, "invalid GitHub identity")
        with self.db.connection() as conn:
            conn.execute("""
                INSERT INTO ecc_public.accounts(github_id,login) VALUES (%s,%s)
                ON CONFLICT(github_id) DO UPDATE SET login=excluded.login, updated_at=now()
            """, (github_id, login))

    def account_keys(self, account_id: int) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute("""
                SELECT id,label,created_at,revoked_at,last_used_at FROM ecc_public.api_keys
                WHERE account_id=%s ORDER BY created_at DESC
            """, (account_id,)).fetchall()

    def account_runs(self, account_id: int, *, include_generic: bool = False, limit: int = 50) -> dict:
        """This account's runs with their recent submission rate, and its latest
        distinguished-point batches across both protocols."""
        with self.db.connection() as conn:
            runs = conn.execute("""
                SELECT r.run_id::text AS id,%s AS campaign,%s AS dp_bits,k.label AS key_label,
                  r.created_at,r.last_seen_at,r.suspended_at,NULL::timestamptz AS closed_at,
                  r.total_bytes/%s AS points,b.last_hour,b.last_day,b.last_batch_at,
                  count(*) OVER () AS all_runs,
                  count(*) FILTER (WHERE r.suspended_at IS NULL
                    AND r.last_seen_at>=now()-interval '15 minutes') OVER () AS all_active,
                  coalesce(sum(r.total_bytes/%s) OVER (),0) AS all_points,
                  coalesce(sum(b.last_hour) OVER (),0) AS all_last_hour,
                  coalesce(sum(b.last_day) OVER (),0) AS all_last_day
                FROM ecc_public.runs r JOIN ecc_public.api_keys k ON k.id=r.key_id
                CROSS JOIN LATERAL (
                  SELECT coalesce(sum(records) FILTER (WHERE created_at>=now()-interval '1 hour'),0) AS last_hour,
                    coalesce(sum(records) FILTER (WHERE created_at>=now()-interval '24 hours'),0) AS last_day,
                    max(created_at) AS last_batch_at
                  FROM ecc_public.batches WHERE run_id=r.run_id) b
                WHERE r.account_id=%s ORDER BY r.last_seen_at DESC LIMIT %s
            """, (CAMPAIGN, DP_WEIGHT, RECORD_BYTES, RECORD_BYTES, account_id, limit)).fetchall()
            groups = [runs]
            recent = conn.execute("""
                SELECT b.created_at,b.run_id::text AS run_id,%s AS campaign,b.records,b.audit_state AS state
                FROM ecc_public.batches b JOIN ecc_public.runs r ON r.run_id=b.run_id
                WHERE r.account_id=%s ORDER BY b.created_at DESC LIMIT 20
            """, (CAMPAIGN, account_id)).fetchall()
            if include_generic:
                generic_runs = conn.execute("""
                    SELECT r.id::text AS id,n.campaign_id AS campaign,
                      (n.manifest->'walk'->>'dp_bits')::int AS dp_bits,k.label AS key_label,
                      r.created_at,r.last_seen_at,r.suspended_at,r.closed_at,
                      r.total_records AS points,b.last_hour,b.last_day,b.last_batch_at,
                      count(*) OVER () AS all_runs,
                      count(*) FILTER (WHERE r.suspended_at IS NULL AND r.closed_at IS NULL
                        AND r.last_seen_at>=now()-interval '15 minutes') OVER () AS all_active,
                      coalesce(sum(r.total_records) OVER (),0) AS all_points,
                      coalesce(sum(b.last_hour) OVER (),0) AS all_last_hour,
                      coalesce(sum(b.last_day) OVER (),0) AS all_last_day
                    FROM ecc_public.dp_runs r
                    JOIN ecc_public.dp_namespaces n ON n.id=r.namespace_id
                    JOIN ecc_public.api_keys k ON k.id=r.key_id
                    CROSS JOIN LATERAL (
                      SELECT coalesce(sum(records) FILTER (WHERE created_at>=now()-interval '1 hour'),0) AS last_hour,
                        coalesce(sum(records) FILTER (WHERE created_at>=now()-interval '24 hours'),0) AS last_day,
                        max(created_at) AS last_batch_at
                      FROM ecc_public.dp_batches
                      WHERE run_id=r.id AND state NOT IN ('reserved','expired','failed')) b
                    WHERE r.account_id=%s ORDER BY r.last_seen_at DESC LIMIT %s
                """, (account_id, limit)).fetchall()
                groups.append(generic_runs)
                runs = runs + generic_runs
                recent += conn.execute("""
                    SELECT b.created_at,b.run_id::text AS run_id,n.campaign_id AS campaign,b.records,b.state
                    FROM ecc_public.dp_batches b
                    JOIN ecc_public.dp_runs r ON r.id=b.run_id
                    JOIN ecc_public.dp_namespaces n ON n.id=b.namespace_id
                    WHERE r.account_id=%s AND b.state NOT IN ('reserved','expired')
                    ORDER BY b.created_at DESC LIMIT 20
                """, (account_id,)).fetchall()
            now = conn.execute("SELECT now() AS now").fetchone()["now"]
        for run in runs:
            age = (now - run["last_seen_at"]).total_seconds()
            run["state"] = ("suspended" if run["suspended_at"] else "closed" if run["closed_at"]
                            else "active" if age < 900 else "idle" if age < 86400 else "stopped")
            run["points_per_hour"] = round(run["last_day"] / 24, 1)
            ops = run["last_day"] * 2 ** run["dp_bits"] / 86400
            run["operations_per_second_log2"] = round(math.log2(ops), 2) if ops else None
        order = {"active": 0, "idle": 1, "stopped": 2, "closed": 3, "suspended": 4}
        runs.sort(key=lambda run: (order[run["state"]], -run["last_seen_at"].timestamp()))
        recent.sort(key=lambda batch: batch["created_at"], reverse=True)
        # Window aggregates cover every account run before either display limit.
        totals = {key: sum(int(group[0][key]) for group in groups if group)
                  for key in ("all_runs", "all_active", "all_points", "all_last_day", "all_last_hour")}
        return {"runs": runs[:limit], "recent": recent[:20],
                "total_runs": totals["all_runs"],
                "active_runs": totals["all_active"],
                "points": totals["all_points"],
                "points_per_hour": round(totals["all_last_day"] / 24, 1),
                "points_last_hour": totals["all_last_hour"]}

    def create_key(self, account_id: int, label: str) -> str:
        label = label.strip()
        if not 1 <= len(label) <= 80:
            raise Rejected(400, "key name must be 1 to 80 characters")
        key_id, value, digest = generate_key(self.settings.key_pepper)
        with self.db.connection() as conn:
            account = conn.execute("SELECT suspended_at FROM ecc_public.accounts WHERE github_id=%s FOR UPDATE",
                                   (account_id,)).fetchone()
            if not account or account["suspended_at"]:
                raise Rejected(403, "account is unavailable")
            row = conn.execute("""
                SELECT count(*) AS active, max(created_at) AS newest
                FROM ecc_public.api_keys WHERE account_id=%s AND revoked_at IS NULL
            """, (account_id,)).fetchone()
            if row["active"] >= 3:
                raise Rejected(429, "revoke an existing key before creating another")
            recent = conn.execute("""
                SELECT EXISTS(SELECT 1 FROM ecc_public.api_keys
                              WHERE account_id=%s AND created_at > now()-interval '1 minute') AS recent
            """, (account_id,)).fetchone()["recent"]
            if recent:
                raise Rejected(429, "wait one minute before creating another key", 60)
            conn.execute("""
                INSERT INTO ecc_public.api_keys(id,account_id,label,digest)
                VALUES (%s,%s,%s,%s)
            """, (key_id, account_id, label, digest))
        return value

    def revoke_key(self, account_id: int, key_id: str) -> None:
        with self.db.connection() as conn:
            row = conn.execute("""
                UPDATE ecc_public.api_keys SET revoked_at=coalesce(revoked_at,now())
                WHERE id=%s AND account_id=%s RETURNING id
            """, (key_id, account_id)).fetchone()
            if not row:
                raise Rejected(404, "key not found")
            conn.execute("""
                UPDATE ecc_public.runs SET suspended_at=coalesce(suspended_at,now())
                WHERE key_id=%s
            """, (key_id,))
            conn.execute("""
                UPDATE ecc_public.dp_runs SET suspended_at=coalesce(suspended_at,now())
                WHERE key_id=%s
            """, (key_id,))

    def authenticate(self, bearer: str, cost: int = 1) -> dict:
        parsed = parse_and_digest(self.settings.key_pepper, bearer)
        if not parsed:
            raise Rejected(401, "invalid API key")
        key_id, digest = parsed
        with self.db.connection() as conn:
            row = conn.execute("""
                SELECT k.id,k.account_id,k.digest,k.revoked_at,a.suspended_at
                FROM ecc_public.api_keys k JOIN ecc_public.accounts a ON a.github_id=k.account_id
                WHERE k.id=%s FOR UPDATE OF k
            """, (key_id,)).fetchone()
            if not row or not same_digest(bytes(row["digest"]), digest) or row["revoked_at"] or row["suspended_at"]:
                raise Rejected(401, "invalid API key")
            # Atomic, credential-level token bucket. A single 1 MiB batch costs 16 tokens.
            limited = conn.execute("""
                UPDATE ecc_public.api_keys
                SET tokens=least(240, tokens + greatest(0,extract(epoch from now()-token_updated_at))*4)-%s,
                    token_updated_at=now(), last_used_at=now()
                WHERE id=%s AND least(240, tokens + greatest(0,extract(epoch from now()-token_updated_at))*4)>=%s
                  AND revoked_at IS NULL
                RETURNING id
            """, (cost, key_id, cost)).fetchone()
            if not limited:
                raise Rejected(429, "API key rate limit exceeded", 15)
            return {"id": key_id, "account_id": row["account_id"]}

    def _active_run(self, conn, run_id: int, key: dict, lock: bool = False) -> dict:
        suffix = " FOR UPDATE OF r" if lock else ""
        row = conn.execute("""
            SELECT r.* FROM ecc_public.runs r JOIN ecc_public.api_keys k ON k.id=r.key_id
            WHERE r.run_id=%s AND r.account_id=%s AND r.key_id=%s
              AND r.suspended_at IS NULL AND k.revoked_at IS NULL
        """ + suffix, (run_id, key["account_id"], key["id"])).fetchone()
        if not row:
            raise Rejected(401, "run or key is unavailable")
        return row

    def register(self, key: dict, registration_id: str, dp_weight: int = DP_WEIGHT) -> dict:
        if type(dp_weight) is not int or dp_weight not in DP_WEIGHTS:
            raise Rejected(400, "unsupported sigma reporting threshold")
        try:
            nonce = UUID(registration_id)
        except (ValueError, TypeError):
            raise Rejected(400, "X-Registration-Id must be a UUID") from None
        with self.db.connection() as conn:
            prior = conn.execute("""
                SELECT run_id,dp_weight FROM ecc_public.runs WHERE key_id=%s AND registration_id=%s
            """, (key["id"], nonce)).fetchone()
            if prior:
                if prior["dp_weight"] != dp_weight:
                    raise Rejected(409, "registration_id belongs to a different reporting threshold")
                return self._registration_result(prior["run_id"], dp_weight)
            account = conn.execute("""
                SELECT github_id FROM ecc_public.accounts
                WHERE github_id=%s AND suspended_at IS NULL FOR UPDATE
            """, (key["account_id"],)).fetchone()
            if not account:
                raise Rejected(401, "account unavailable")
            prior = conn.execute("""
                SELECT run_id,dp_weight FROM ecc_public.runs WHERE key_id=%s AND registration_id=%s
            """, (key["id"], nonce)).fetchone()
            if prior:
                if prior["dp_weight"] != dp_weight:
                    raise Rejected(409, "registration_id belongs to a different reporting threshold")
                return self._registration_result(prior["run_id"], dp_weight)
            if conn.execute("""
                SELECT 1 FROM ecc_public.dp_runs WHERE key_id=%s AND registration_id=%s
            """, (key["id"], nonce)).fetchone():
                raise Rejected(409, "registration_id belongs to the generic protocol")
            run_counts = conn.execute("""
                SELECT count(*) AS lifetime,
                       count(*) FILTER (WHERE suspended_at IS NULL AND closed_at IS NULL) AS active
                FROM (SELECT suspended_at,NULL::timestamptz AS closed_at
                      FROM ecc_public.runs WHERE account_id=%s
                      UNION ALL
                      SELECT suspended_at,closed_at FROM ecc_public.dp_runs WHERE account_id=%s) AS all_runs
            """, (key["account_id"], key["account_id"])).fetchone()
            if run_counts["lifetime"] >= self.settings.max_account_runs_lifetime:
                raise Rejected(429, "account lifetime run ID limit reached")
            if run_counts["active"] >= 8:
                raise Rejected(429, "account run limit reached")
            for _ in range(self.settings.run_id_end - self.settings.run_id_start + 1):
                try:
                    run_id = conn.execute("SELECT nextval('ecc_public.run_id_seq') AS id").fetchone()["id"]
                except Exception as exc:
                    raise Rejected(503, "run ID range exhausted") from exc
                if not self.settings.run_id_start <= run_id <= self.settings.run_id_end:
                    continue
                if not self.storage.reserve_run(run_id):
                    continue
                conn.execute("""
                    INSERT INTO ecc_public.runs(run_id,account_id,key_id,registration_id,dp_weight)
                    VALUES (%s,%s,%s,%s,%s)
                """, (run_id, key["account_id"], key["id"], nonce, dp_weight))
                return self._registration_result(run_id, dp_weight)
        raise Rejected(503, "run ID range exhausted")

    @staticmethod
    def _registration_result(run_id: int, dp_weight: int = DP_WEIGHT) -> dict:
        return {"run_id": run_id, "campaign": CAMPAIGN, "walk_id": WALK_ID,
                "dp_weight": dp_weight}

    def run_status(self, key: dict, run_id: int) -> dict:
        with self.db.connection() as conn:
            run = self._active_run(conn, run_id, key)
            return {"run_id": run_id, "cursor": run["cursor"],
                    "checkpoint_at": run["checkpoint_at"], "dp_weight": run["dp_weight"],
                    "campaign": CAMPAIGN, "walk_id": WALK_ID}

    def _charge_daily_bytes(self, conn, key: dict, length: int) -> None:
        quota = conn.execute("""
            INSERT INTO ecc_public.key_usage_day(key_id,day,bytes)
            SELECT %s,current_date,%s WHERE %s<=%s
            ON CONFLICT(key_id,day) DO UPDATE SET bytes=ecc_public.key_usage_day.bytes+excluded.bytes
            WHERE ecc_public.key_usage_day.bytes+excluded.bytes<=%s
            RETURNING bytes
        """, (key["id"], length, length, self.settings.max_key_bytes_per_day,
              self.settings.max_key_bytes_per_day)).fetchone()
        if not quota:
            raise Rejected(429, "daily upload quota reached", 3600)

    def upload_batch(self, key: dict, run_id: int, offset: int, data: bytes) -> dict:
        if offset < 0 or offset % RECORD_BYTES:
            raise Rejected(400, "invalid byte offset")
        with self.db.connection() as conn:
            dp_weight = self._active_run(conn, run_id, key)["dp_weight"]
        try:
            count = check_records(data, run_id, dp_weight)
        except ValueError as exc:
            raise Rejected(400, str(exc)) from exc
        digest = hashlib.sha256(data).hexdigest()
        object_key = self.storage.batch_key(run_id, offset, digest)
        with self.db.connection() as conn:
            run = self._active_run(conn, run_id, key, lock=True)
            prior = conn.execute("""
                SELECT sha256,byte_length FROM ecc_public.batches WHERE run_id=%s AND byte_offset=%s
            """, (run_id, offset)).fetchone()
            if prior:
                if prior["sha256"] == digest and prior["byte_length"] == len(data):
                    return {"ok": True, "duplicate": True, "cursor": run["cursor"]}
                raise Rejected(409, "offset already holds different bytes")
            if offset != run["cursor"]:
                raise Rejected(409, "offset does not equal server cursor")
            shard = run_id % 16
            shard_limit = max(1, self.settings.max_pending_batches // 16)
            pressure = conn.execute("""
                SELECT pending_batches FROM ecc_public.pressure_shards WHERE shard=%s
            """, (shard,)).fetchone()
            if not pressure or pressure["pending_batches"] >= shard_limit:
                raise Rejected(503, "verification backlog is full; pause and retry", 60)
            self._charge_daily_bytes(conn, key, len(data))
            # The per-run lock serializes offsets. Do not lock the shared pressure
            # shard across network I/O: that would serialize unrelated runs.
            self.storage.put_immutable(object_key, data)
            pressure = conn.execute("""
                UPDATE ecc_public.pressure_shards
                SET pending_batches=pending_batches+1,pending_bytes=pending_bytes+%s
                WHERE shard=%s AND pending_batches < %s RETURNING shard
            """, (len(data), shard, shard_limit)).fetchone()
            if not pressure:
                # A concurrent upload filled the shard after the preflight.
                # The immutable S3 object can be reused by an exact retry.
                raise Rejected(503, "verification backlog is full; pause and retry", 60)
            row = conn.execute("""
                INSERT INTO ecc_public.batches(run_id,byte_offset,byte_length,records,sha256,object_key)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
            """, (run_id, offset, len(data), count, digest, object_key)).fetchone()
            conn.execute("""
                UPDATE ecc_public.runs SET cursor=cursor+%s,total_bytes=total_bytes+%s,last_seen_at=now()
                WHERE run_id=%s
            """, (len(data), len(data), run_id))
            batch_id = row["id"]
        # A failed send is repaired by the outbox reconciler. The S3+DB commit is the ACK.
        try:
            self.storage.send(self.settings.audit_queue_url, batch_id)
            with self.db.connection() as conn:
                conn.execute("UPDATE ecc_public.batches SET audit_enqueued_at=now() WHERE id=%s", (batch_id,))
        except Exception:
            pass
        return {"ok": True, "records": count, "cursor": offset + len(data)}

    def upload_checkpoint(self, key: dict, run_id: int, offset: int, data: bytes) -> dict:
        with self.db.connection() as conn:
            dp_weight = self._active_run(conn, run_id, key)["dp_weight"]
        try:
            iterations = check_checkpoint(data, run_id, dp_weight)
        except ValueError as exc:
            raise Rejected(400, str(exc)) from exc
        digest = hashlib.sha256(data).hexdigest()
        object_key = self.storage.checkpoint_key(run_id, digest)
        with self.db.connection() as conn:
            run = self._active_run(conn, run_id, key, lock=True)
            if offset != run["cursor"]:
                raise Rejected(409, "checkpoint offset must equal DP cursor")
            if run["checkpoint_key"] == object_key:
                return {"ok": True, "sha256": digest, "iterations": iterations, "cursor": offset}
            self._charge_daily_bytes(conn, key, len(data))
            self.storage.put_immutable(object_key, data)
            conn.execute("""
                UPDATE ecc_public.runs SET checkpoint_key=%s,checkpoint_at=%s,last_seen_at=now()
                WHERE run_id=%s
            """, (object_key, offset, run_id))
        return {"ok": True, "sha256": digest, "iterations": iterations, "cursor": offset}

    def download_checkpoint(self, key: dict, run_id: int) -> tuple[bytes, int]:
        with self.db.connection() as conn:
            run = self._active_run(conn, run_id, key)
            if not run["checkpoint_key"]:
                raise Rejected(404, "checkpoint unavailable")
            return self.storage.get(run["checkpoint_key"]), run["checkpoint_at"]
