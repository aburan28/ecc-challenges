"""SQS outbox repair, independent seed audits, and idempotent bulk indexing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

from .protocol import CAMPAIGN, DP_WEIGHT, check_records


def _seed_bytes(seed: int) -> bytes:
    return seed.to_bytes(max(1, (seed.bit_length() + 7) // 8), "big")


def batch_records(body: bytes, run_id: int, dp_weight: int = DP_WEIGHT):
    check_records(body, run_id, dp_weight)
    for i in range(len(body) // 32):
        row = body[32 * i:32 * (i + 1)]
        seed = int.from_bytes(row[:8], "little")
        yield (i, seed, _seed_bytes(seed), row[8:])


class QueueWorker:
    def __init__(self, db, storage, settings, sqs):
        self.db, self.storage, self.settings, self.sqs = db, storage, settings, sqs
        self.verifier_sha = hashlib.sha256(open(settings.replay_binary, "rb").read()).hexdigest() if os.path.isfile(settings.replay_binary) else None

    def dispatch_trusted_audits(self, limit: int = 100) -> int:
        """Select sealed trusted-run audits without waiting for replay capacity.

        Admission already validates record structure before sealing. Indexing
        independently checks the archive digest and structure. First-run audits
        and any selection already made by a replay worker remain untouched.
        """
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT b.id,b.records FROM ecc_public.batches b
                JOIN ecc_public.runs r ON r.run_id=b.run_id
                WHERE b.audit_state='pending' AND b.audit_indices IS NULL
                  AND r.trusted_at IS NOT NULL AND r.suspended_at IS NULL
                ORDER BY b.id LIMIT %s FOR UPDATE OF b SKIP LOCKED
            """, (limit,)).fetchall()
            for row in rows:
                indices = [secrets.randbelow(row["records"])] if secrets.randbelow(128) == 0 else []
                conn.execute("""
                    UPDATE ecc_public.batches
                    SET audit_indices=%s::jsonb,audit_started_at=now(),
                        audit_state=CASE WHEN %s THEN 'pending' ELSE 'not_selected' END,
                        audit_finished_at=CASE WHEN %s THEN NULL ELSE now() END
                    WHERE id=%s AND audit_state='pending' AND audit_indices IS NULL
                """, (json.dumps(indices), bool(indices), bool(indices), row["id"]))
        return len(rows)

    def release_unverified_pressure(self, limit: int = 100) -> int:
        """Keep terminal audit evidence, but release its active queue capacity.

        A timed-out audit is not queued for automatic retry and cannot be
        indexed. Its immutable archive and unverified state remain unchanged.
        Row locking and the receipt make repeated reconciliation idempotent.
        """
        with self.db.connection() as conn:
            rows = conn.execute("""
                WITH terminal AS (
                    SELECT id FROM ecc_public.batches
                    WHERE audit_state='unverified' AND pressure_released_at IS NULL
                    ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED
                )
                UPDATE ecc_public.batches b SET pressure_released_at=now()
                FROM terminal t WHERE b.id=t.id AND b.pressure_released_at IS NULL
                RETURNING b.run_id,b.byte_length
            """, (limit,)).fetchall()
            totals = {}
            for row in rows:
                shard = row["run_id"] % 16
                count, size = totals.get(shard, (0, 0))
                totals[shard] = (count + 1, size + row["byte_length"])
            for shard, (count, size) in sorted(totals.items()):
                conn.execute("""
                    UPDATE ecc_public.pressure_shards
                    SET pending_batches=pending_batches-%s,pending_bytes=pending_bytes-%s
                    WHERE shard=%s
                """, (count, size, shard))
        return len(rows)

    def repair_outbox(self, limit: int = 100) -> int:
        """Safe after S3/DB commit or process death; duplicate SQS sends are expected."""
        self.dispatch_trusted_audits(limit)
        self.release_unverified_pressure(limit)
        sent = 0
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT id FROM ecc_public.batches WHERE audit_state='pending'
                  AND (audit_enqueued_at IS NULL OR audit_enqueued_at < now()-interval '4 hours')
                ORDER BY id LIMIT %s
            """, (limit,)).fetchall()
        for row in rows:
            self.storage.send(self.settings.audit_queue_url, row["id"])
            with self.db.connection() as conn:
                conn.execute("UPDATE ecc_public.batches SET audit_enqueued_at=now() WHERE id=%s", (row["id"],))
            sent += 1
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT b.id FROM ecc_public.batches b JOIN ecc_public.runs r ON r.run_id=b.run_id
                WHERE b.audit_state IN ('sampled','not_selected') AND r.trusted_at IS NOT NULL
                  AND r.suspended_at IS NULL
                  AND (b.index_enqueued_at IS NULL OR b.index_enqueued_at < now()-interval '30 minutes')
                  AND b.indexed_at IS NULL
                ORDER BY b.id LIMIT %s
            """, (limit,)).fetchall()
        for row in rows:
            self.storage.send(self.settings.index_queue_url, row["id"])
            with self.db.connection() as conn:
                conn.execute("UPDATE ecc_public.batches SET index_enqueued_at=now() WHERE id=%s", (row["id"],))
            sent += 1
        if self.settings.collision_queue_url:
            with self.db.connection() as conn:
                rows = conn.execute("""
                    SELECT c.id FROM ecc_public.collision_leads c
                    JOIN ecc_public.runs r ON r.run_id=c.run_id
                    WHERE c.replay_state='pending' AND c.quarantined_at IS NULL
                      AND (c.resolve_enqueued_at IS NULL OR c.resolve_enqueued_at < now()-interval '4 hours')
                      AND (c.resolve_lease_until IS NULL OR c.resolve_lease_until < now())
                      AND r.suspended_at IS NULL
                    ORDER BY c.id LIMIT %s
                """, (limit,)).fetchall()
            for row in rows:
                self.storage.send_lead(self.settings.collision_queue_url, row["id"])
                with self.db.connection() as conn:
                    conn.execute("UPDATE ecc_public.collision_leads SET resolve_enqueued_at=now() WHERE id=%s", (row["id"],))
                sent += 1
        return sent

    def audit_one(self, batch_id: int, timeout_seconds: int = 3600) -> str:
        with self.db.connection() as conn:
            row = conn.execute("""
                SELECT b.*,r.trusted_at,r.suspended_at,r.dp_weight FROM ecc_public.batches b
                JOIN ecc_public.runs r ON r.run_id=b.run_id WHERE b.id=%s
            """, (batch_id,)).fetchone()
        if row is None or row["audit_state"] != "pending":
            return "already_done"
        body = self.storage.get(row["object_key"])
        if len(body) != row["byte_length"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
            self._fail_batch(batch_id, row["run_id"], "archive_digest_mismatch")
            return "failed"
        try:
            check_records(body, row["run_id"], row["dp_weight"])
        except ValueError:
            self._fail_batch(batch_id, row["run_id"], "structure_mismatch")
            return "failed"
        # The choice happens after S3 sealing, and is stored before replay.
        # First batch of a run is always sampled. Later batches are sampled
        # independently with probability 1/128; unselected remains labeled.
        with self.db.connection() as conn:
            locked = conn.execute("""
                SELECT b.audit_indices,r.trusted_at FROM ecc_public.batches b
                JOIN ecc_public.runs r ON r.run_id=b.run_id WHERE b.id=%s FOR UPDATE OF b
            """, (batch_id,)).fetchone()
            if locked["audit_indices"] is None:
                if locked["trusted_at"] is None or secrets.randbelow(128) == 0:
                    indices = [secrets.randbelow(row["records"])]
                else:
                    indices = []
                conn.execute("UPDATE ecc_public.batches SET audit_indices=%s::jsonb,audit_started_at=now() WHERE id=%s",
                             (json.dumps(indices), batch_id))
            else:
                indices = locked["audit_indices"]
        if not indices:
            with self.db.connection() as conn:
                conn.execute("UPDATE ecc_public.batches SET audit_state='not_selected',audit_finished_at=now() WHERE id=%s AND audit_state='pending'", (batch_id,))
            return "not_selected"
        if not self.verifier_sha:
            raise RuntimeError("exact verifier binary is unavailable")
        for index in indices:
            record = body[index * 32:(index + 1) * 32]
            started = time.monotonic()
            try:
                result = subprocess.run([self.settings.replay_binary, "--dp-weight", str(row["dp_weight"])], input=record,
                                        capture_output=True, timeout=timeout_seconds, check=False)
                outcome = "pass" if result.returncode == 0 else "mismatch" if result.returncode == 2 else "error"
            except subprocess.TimeoutExpired:
                outcome = "timeout"
            elapsed_ms = int((time.monotonic() - started) * 1000)
            with self.db.connection() as conn:
                conn.execute("""
                    INSERT INTO ecc_public.audit_events(batch_id,record_index,record_sha256,verifier_sha256,outcome,elapsed_ms)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
                """, (batch_id, index, hashlib.sha256(record).hexdigest(), self.verifier_sha, outcome, elapsed_ms))
            if outcome == "mismatch":
                self._fail_batch(batch_id, row["run_id"], "seed_replay_mismatch")
                return "failed"
            if outcome in ("error", "timeout"):
                with self.db.connection() as conn:
                    conn.execute("UPDATE ecc_public.batches SET audit_state='unverified',audit_finished_at=now() WHERE id=%s", (batch_id,))
                return "unverified"
        with self.db.connection() as conn:
            conn.execute("UPDATE ecc_public.batches SET audit_state='sampled',audit_source_sha256=%s,audit_finished_at=now() WHERE id=%s AND audit_state='pending'",
                         (self.verifier_sha, batch_id))
            conn.execute("UPDATE ecc_public.runs SET trusted_at=coalesce(trusted_at,now()) WHERE run_id=%s", (row["run_id"],))
        return "sampled"

    def _fail_batch(self, batch_id: int, run_id: int, reason: str) -> None:
        with self.db.connection() as conn:
            conn.execute("UPDATE ecc_public.runs SET suspended_at=coalesce(suspended_at,now()) WHERE run_id=%s", (run_id,))
            conn.execute("UPDATE ecc_public.batches SET audit_state='failed',audit_finished_at=now() WHERE id=%s", (batch_id,))
            conn.execute("""
                UPDATE ecc_public.batches SET audit_state='quarantined',audit_finished_at=now()
                WHERE run_id=%s AND id<>%s AND audit_state='pending'
            """, (run_id, batch_id))
            unreleased = conn.execute("""
                SELECT count(*) AS n,coalesce(sum(byte_length),0) AS bytes
                FROM ecc_public.batches WHERE run_id=%s AND pressure_released_at IS NULL
            """, (run_id,)).fetchone()
            conn.execute("""
                UPDATE ecc_public.batches SET pressure_released_at=now()
                WHERE run_id=%s AND pressure_released_at IS NULL
            """, (run_id,))
            conn.execute("""
                UPDATE ecc_public.pressure_shards
                SET pending_batches=pending_batches-%s,pending_bytes=pending_bytes-%s
                WHERE shard=%s
            """, (unreleased["n"], unreleased["bytes"], run_id % 16))
            conn.execute("UPDATE ecc_public.candidate_points SET disqualified_at=now() WHERE first_run=%s AND disqualified_at IS NULL", (run_id,))
            conn.execute("UPDATE ecc_public.collision_leads SET quarantined_at=now() WHERE run_id=%s AND quarantined_at IS NULL", (run_id,))
            conn.execute("""
                UPDATE ecc_public.api_keys SET revoked_at=coalesce(revoked_at,now())
                WHERE id=(SELECT key_id FROM ecc_public.runs WHERE run_id=%s)
            """, (run_id,))
        print(json.dumps({"event": "audit_failed", "batch_id": batch_id, "reason": reason}), flush=True)

    def index_one(self, batch_id: int) -> str:
        with self.db.connection() as conn:
            row = conn.execute("""
                SELECT b.*,r.trusted_at,r.suspended_at,r.dp_weight FROM ecc_public.batches b
                JOIN ecc_public.runs r ON r.run_id=b.run_id WHERE b.id=%s
            """, (batch_id,)).fetchone()
        if row is None or row["indexed_at"] is not None:
            return "already_done"
        if row["suspended_at"] is not None or row["audit_state"] in ("failed", "quarantined"):
            return "discarded"
        if row["audit_state"] not in ("sampled", "not_selected") or row["trusted_at"] is None:
            return "not_ready"
        body = self.storage.get(row["object_key"])
        if len(body) != row["byte_length"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise RuntimeError("sealed object changed or is missing")
        records = list(batch_records(body, row["run_id"], row["dp_weight"]))
        with self.db.connection() as conn:
            run_state = conn.execute("""
                SELECT trusted_at,suspended_at FROM ecc_public.runs WHERE run_id=%s
            """, (row["run_id"],)).fetchone()
            if run_state["suspended_at"] is not None or run_state["trusted_at"] is None:
                return "discarded"
            locked = conn.execute("SELECT indexed_at,audit_state FROM ecc_public.batches WHERE id=%s FOR UPDATE",
                                  (batch_id,)).fetchone()
            if locked["indexed_at"] is not None:
                return "already_done"
            if locked["audit_state"] not in ("sampled", "not_selected"):
                return "not_ready"
            conn.execute("CREATE TEMP TABLE incoming(idx integer,seed numeric(20,0),seed_bytes bytea,point_key bytea) ON COMMIT DROP")
            with conn.cursor().copy("COPY incoming(idx,seed,seed_bytes,point_key) FROM STDIN") as copy:
                for record in records:
                    copy.write_row(record)
            conn.execute("ANALYZE incoming")
            # Insert before lookup. A concurrent ON CONFLICT can wait for a
            # winning row absent from its statement snapshot. The following
            # separate statement gets a fresh READ COMMITTED snapshot and sees
            # that winner, so simultaneous first encounters retain the lead.
            inserted = conn.execute("""
                WITH first AS (SELECT DISTINCT ON(point_key) point_key,seed FROM incoming ORDER BY point_key,idx),
                ins AS (
                  INSERT INTO ecc_public.candidate_points(campaign_id,point_key,first_seed,first_run,first_batch)
                  SELECT %s,point_key,seed,%s,%s FROM first ORDER BY point_key
                  ON CONFLICT (campaign_id,point_key) DO UPDATE
                  SET first_seed=excluded.first_seed,first_run=excluded.first_run,
                      first_batch=excluded.first_batch,created_at=now(),disqualified_at=NULL
                  WHERE ecc_public.candidate_points.disqualified_at IS NOT NULL
                  RETURNING 1
                ) SELECT count(*) AS n FROM ins
            """, (CAMPAIGN, row["run_id"], batch_id)).fetchone()["n"]
            # Campaign matches are leads, not accepted solutions. A null old
            # seed is retained as an unresolved lead for later source replay.
            conn.execute("""
                INSERT INTO ecc_public.collision_leads(batch_id,record_index,run_id,seed,point_key,prior_source,prior_seed_bytes,prior_worker)
                SELECT %s,i.idx,%s,i.seed,i.point_key,'campaign',d.walk_seed,d.worker_id
                FROM incoming i JOIN public.distinguished_points d
                  ON d.campaign_id=%s AND d.point_key=i.point_key
                WHERE d.walk_seed IS DISTINCT FROM i.seed_bytes
                ON CONFLICT DO NOTHING
            """, (batch_id, row["run_id"], CAMPAIGN))
            conn.execute("""
                INSERT INTO ecc_public.collision_leads(batch_id,record_index,run_id,seed,point_key,prior_source,prior_seed,prior_batch)
                SELECT %s,i.idx,%s,i.seed,i.point_key,'public',c.first_seed,c.first_batch
                FROM incoming i JOIN ecc_public.candidate_points c
                  ON c.campaign_id=%s AND c.point_key=i.point_key AND c.disqualified_at IS NULL
                WHERE c.first_seed<>i.seed
                ON CONFLICT DO NOTHING
            """, (batch_id, row["run_id"], CAMPAIGN))
            conn.execute("""
                INSERT INTO ecc_public.collision_leads(batch_id,record_index,run_id,seed,point_key,prior_source,prior_seed,prior_batch)
                SELECT DISTINCT ON(i.idx) %s,i.idx,%s,i.seed,i.point_key,'public',j.seed,%s
                FROM incoming i JOIN incoming j ON i.point_key=j.point_key AND j.idx<i.idx AND j.seed<>i.seed
                ORDER BY i.idx,j.idx
                ON CONFLICT DO NOTHING
            """, (batch_id, row["run_id"], batch_id))
            leads = conn.execute("SELECT count(*) AS n FROM ecc_public.collision_leads WHERE batch_id=%s",
                                 (batch_id,)).fetchone()["n"]
            # Keep slow historical joins outside the run-row lock used by
            # uploads. Recheck immediately before publication: suspension
            # during the joins must roll back every candidate and lead.
            final_state = conn.execute("""
                SELECT trusted_at,suspended_at FROM ecc_public.runs WHERE run_id=%s FOR SHARE
            """, (row["run_id"],)).fetchone()
            if final_state["suspended_at"] is not None or final_state["trusted_at"] is None:
                conn.rollback()
                return "discarded"
            conn.execute("""
                INSERT INTO ecc_public.indexed_batches(batch_id,records,candidate_new,leads)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
            """, (batch_id, len(records), inserted, leads))
            released = conn.execute("""
                UPDATE ecc_public.batches SET pressure_released_at=now()
                WHERE id=%s AND pressure_released_at IS NULL RETURNING id
            """, (batch_id,)).fetchone()
            conn.execute("UPDATE ecc_public.batches SET indexed_at=now() WHERE id=%s", (batch_id,))
            if released:
                conn.execute("""
                    UPDATE ecc_public.pressure_shards
                    SET pending_batches=pending_batches-1,pending_bytes=pending_bytes-%s
                    WHERE shard=%s
                """, (len(body), row["run_id"] % 16))
        return "indexed"

    def resolve_one(self, lead_id: int, timeout_seconds: int = 3600) -> str:
        """Rewalk both colliding seeds, then run the pinned host scalar solver."""
        with self.db.connection() as conn:
            row = conn.execute("""
                UPDATE ecc_public.collision_leads
                SET resolve_started_at=now(),resolve_lease_until=now()+interval '4 hours'
                WHERE id=%s AND replay_state='pending' AND quarantined_at IS NULL
                  AND (resolve_lease_until IS NULL OR resolve_lease_until<now())
                RETURNING *
            """, (lead_id,)).fetchone()
        if row is None:
            with self.db.connection() as conn:
                state = conn.execute("SELECT replay_state,quarantined_at FROM ecc_public.collision_leads WHERE id=%s",
                                     (lead_id,)).fetchone()
            if state is None or state["replay_state"] != "pending" or state["quarantined_at"]:
                return "already_done"
            return "not_ready"

        with self.db.connection() as conn:
            current = conn.execute("SELECT b.*,r.dp_weight FROM ecc_public.batches b JOIN ecc_public.runs r ON r.run_id=b.run_id WHERE b.id=%s", (row["batch_id"],)).fetchone()
            prior = (conn.execute("SELECT b.*,r.dp_weight FROM ecc_public.batches b JOIN ecc_public.runs r ON r.run_id=b.run_id WHERE b.id=%s", (row["prior_batch"],)).fetchone()
                     if row["prior_batch"] else None)
        receipt = {"lead_id": lead_id, "campaign": CAMPAIGN, "point_key": bytes(row["point_key"]).hex(),
                   "current_seed": str(row["seed"]), "prior_source": row["prior_source"],
                   "prior_seed": str(row["prior_seed"]) if row["prior_seed"] is not None else None,
                   "prior_seed_bytes": (bytes(row["prior_seed_bytes"]).hex()
                                        if row["prior_seed_bytes"] is not None else None),
                   "current_batch_sha256": current["sha256"],
                   "prior_batch_sha256": prior["sha256"] if prior else None,
                   "replay_binary_sha256": self.verifier_sha,
                   "solver_binary_sha256": (hashlib.sha256(Path(self.settings.solver_binary).read_bytes()).hexdigest()
                                            if os.path.isfile(self.settings.solver_binary) else None)}
        state = "unverified"
        try:
            current_weight = current["dp_weight"]
            prior_weight = prior["dp_weight"] if prior else None
            current_body = self.storage.get(current["object_key"])
            if hashlib.sha256(current_body).hexdigest() != current["sha256"]:
                raise RuntimeError("current sealed object digest mismatch")
            current_record = current_body[row["record_index"] * 32:(row["record_index"] + 1) * 32]
            expected_current = int(row["seed"]).to_bytes(8, "little") + bytes(row["point_key"])
            if current_record != expected_current:
                raise RuntimeError("current record differs from collision lead")
            if row["prior_source"] == "public":
                if prior is None or row["prior_seed"] is None:
                    raise RuntimeError("public prior batch or seed is missing")
                prior_body = self.storage.get(prior["object_key"])
                if hashlib.sha256(prior_body).hexdigest() != prior["sha256"]:
                    raise RuntimeError("prior sealed object digest mismatch")
                prior_record = int(row["prior_seed"]).to_bytes(8, "little") + bytes(row["point_key"])
                if prior_record not in tuple(prior_body[i:i + 32] for i in range(0, len(prior_body), 32)):
                    raise RuntimeError("prior record is absent from its sealed batch")
            else:
                seed_bytes = row["prior_seed_bytes"]
                if seed_bytes is None or not 1 <= len(seed_bytes) <= 8:
                    raise RuntimeError("campaign prior seed is unavailable")
                prior_seed = int.from_bytes(seed_bytes, "big")
                prior_record = prior_seed.to_bytes(8, "little") + bytes(row["point_key"])
                receipt["prior_seed"] = str(prior_seed)
                with self.db.connection() as conn:
                    historical = conn.execute("""
                        SELECT source_dp_weight FROM public.distinguished_points
                        WHERE campaign_id=%s AND point_key=%s AND walk_seed=%s
                    """, (CAMPAIGN, row["point_key"], seed_bytes)).fetchone()
                if historical is None:
                    raise RuntimeError("historical witness is no longer present")
                prior_weight = historical["source_dp_weight"]
            receipt["current_dp_weight"] = current_weight
            receipt["prior_dp_weight"] = prior_weight
            if not self.verifier_sha or (current_weight == prior_weight == 32 and
                                         not receipt["solver_binary_sha256"]):
                raise RuntimeError("pinned replay or scalar solver binary is unavailable")
            replay_results = []
            for label, record in (("current", current_record), ("prior", prior_record)):
                weight = current_weight if label == "current" else prior_weight
                replay_args = (["--dp-weight", str(weight)] if weight is not None else ["--until-endpoint"])
                proc = subprocess.run([self.settings.replay_binary, *replay_args], input=record,
                                      capture_output=True, timeout=timeout_seconds, check=False)
                replay_results.append({"label": label, "record_sha256": hashlib.sha256(record).hexdigest(),
                                       "returncode": proc.returncode,
                                       "stdout": proc.stdout.decode(errors="replace"),
                                       "stderr": proc.stderr.decode(errors="replace")})
                if proc.returncode == 2:
                    if label == "current":
                        self._fail_batch(row["batch_id"], row["run_id"], "collision_replay_mismatch")
                    elif row["prior_source"] == "public" and prior:
                        self._fail_batch(prior["id"], prior["run_id"], "collision_replay_mismatch")
                    state = "rejected"
                    break
                if proc.returncode != 0:
                    raise RuntimeError(f"{label} replay process exited {proc.returncode}")
            receipt["replays"] = replay_results
            if state != "rejected" and (current_weight != 32 or prior_weight != 32):
                result = self.solve_compatibility(current_record, prior_record, current_weight,
                                                  prior_weight, timeout_seconds)
                receipt["compatibility_solver"] = result
                state = "verified" if result["verified"] else "unverified"
            elif state != "rejected":
                with tempfile.TemporaryDirectory(prefix="ecc-collision-") as directory:
                    pair = Path(directory) / "pair.bin"
                    pair.write_bytes(current_record + prior_record)
                    command = [self.settings.solver_binary, "--curve", "131", "--threads", "1",
                               "--steps", "1", "--launches", "1", "--verify", "0",
                               "--run-id", "65535", "--load", str(pair), "--dp-weight", "32",
                               "--max-iters", "4294967296"]
                    proc = subprocess.run(command, capture_output=True, timeout=timeout_seconds, check=False)
                output = (proc.stdout + proc.stderr).decode(errors="replace")
                scalar = next((line.strip()[4:].strip() for line in output.splitlines()
                               if line.strip().startswith("k = ")), None)
                host_verified = (proc.returncode == 0 and scalar is not None and
                                 "verified [k]P == Q" in output and
                                 "matches the published solution: NO" not in output)
                receipt["solver"] = {"argv": [*command[:command.index("--load") + 1], "<sealed-pair>",
                                             *command[command.index("--load") + 2:]],
                                     "returncode": proc.returncode, "stdout": proc.stdout.decode(errors="replace"),
                                     "stderr": proc.stderr.decode(errors="replace"),
                                     "scalar": scalar, "host_verified": host_verified, "verified": False}
                if host_verified:
                    independent = subprocess.run([self.settings.replay_binary, "--verify-scalar"],
                                                 input=(scalar + "\n").encode(), capture_output=True,
                                                 timeout=120, check=False)
                    try:
                        scalar_match = (independent.returncode == 0 and
                                        json.loads(independent.stdout).get("scalar_match") is True)
                    except (ValueError, TypeError):
                        scalar_match = False
                    receipt["independent_scalar"] = {
                        "source_sha256": self.verifier_sha, "returncode": independent.returncode,
                        "stdout": independent.stdout.decode(errors="replace"),
                        "stderr": independent.stderr.decode(errors="replace"),
                        "match": scalar_match,
                    }
                    receipt["solver"]["verified"] = scalar_match
                verified = receipt["solver"]["verified"]
                state = "verified" if verified else "unverified"
        except subprocess.TimeoutExpired as exc:
            receipt["error"] = {"kind": "timeout", "program": str(exc.cmd)}
        except Exception as exc:
            receipt["error"] = {"kind": type(exc).__name__, "message": str(exc)}
        receipt["state"] = state
        encoded = json.dumps(receipt, sort_keys=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        result_key = self.storage.collision_key(lead_id, digest)
        self.storage.put_immutable(result_key, encoded)
        with self.db.connection() as conn:
            conn.execute("""
                UPDATE ecc_public.collision_leads
                SET replay_state=%s,resolve_finished_at=now(),resolve_lease_until=NULL,result_key=%s
                WHERE id=%s AND replay_state='pending'
            """, (state, result_key, lead_id))
        return state

    def solve_compatibility(self, current: bytes, prior: bytes, current_weight: int,
                            prior_weight: int | None, timeout_seconds: float) -> dict:
        """Both seeds reach the named orbit, with separate stopping rules."""
        binary = Path(self.settings.compatibility_binary)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        command = [str(binary), "solve", str(current_weight),
                   str(prior_weight) if prior_weight is not None else "unknown"]
        deadline = time.monotonic() + timeout_seconds
        proc = subprocess.run(command, input=current + prior, capture_output=True,
                              timeout=timeout_seconds, check=False)
        result = {"verified": False, "binary_sha256": digest, "argv": command,
                  "returncode": proc.returncode, "stdout": proc.stdout.decode(errors="replace"),
                  "stderr": proc.stderr.decode(errors="replace")}
        if proc.returncode != 0:
            return result
        answer = json.loads(proc.stdout)
        scalar = answer.get("scalar")
        if answer.get("verified") is not True or not isinstance(scalar, str) or not scalar.isdecimal():
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        independent = subprocess.run([self.settings.replay_binary, "--verify-scalar"],
                                     input=(scalar + "\n").encode(), capture_output=True,
                                     timeout=min(120, remaining), check=False)
        result["scalar"] = scalar
        result["independent_scalar"] = {"binary_sha256": self.verifier_sha,
                                        "returncode": independent.returncode,
                                        "stdout": independent.stdout.decode(errors="replace")}
        result["verified"] = (independent.returncode == 0 and
                               json.loads(independent.stdout).get("scalar_match") is True)
        return result

    def poll(self, kind: str) -> None:
        queue_url = {"audit": self.settings.audit_queue_url,
                     "index": self.settings.index_queue_url,
                     "resolve": self.settings.collision_queue_url}[kind]
        if not queue_url:
            raise ValueError(f"{kind} queue URL is missing")
        while True:
            response = self.sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1,
                                                WaitTimeSeconds=20,
                                                VisibilityTimeout=14400 if kind == "resolve" else 7200)
            for message in response.get("Messages", []):
                item_id = None
                try:
                    payload = json.loads(message["Body"])
                    if "v2_batch_id" in payload:
                        from uuid import UUID
                        item_id = str(UUID(payload["v2_batch_id"]))
                        if kind != "audit" or not getattr(self, "multicurve", None):
                            raise RuntimeError("v2 verification pipeline is unavailable")
                        outcome = self.multicurve.process(item_id)
                    else:
                        item_id = int(payload["lead_id" if kind == "resolve" else "batch_id"])
                        outcome = (self.audit_one(item_id) if kind == "audit" else
                                   self.index_one(item_id) if kind == "index" else self.resolve_one(item_id))
                    if outcome == "not_ready":
                        continue
                    self.sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
                    print(json.dumps({"kind": kind, "item_id": item_id, "outcome": outcome}), flush=True)
                except Exception as exc:
                    # SQS redelivery is safe: all progress and evidence are idempotent.
                    print(json.dumps({"kind": kind, "item_id": item_id, "error": type(exc).__name__}), flush=True)


def main() -> None:
    import argparse
    import boto3
    from botocore.config import Config
    from .db import Database
    from .settings import Settings
    from .storage import Storage
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("audit", "index", "resolve", "reconcile", "merge", "bridge"))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--target-weight", type=int, choices=(30, 32), default=32)
    parser.add_argument("--max-extra-steps", type=int, default=2**24)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    settings = Settings.from_env(web=False)
    db = Database(settings.database_url, max_size=2, statement_timeout_ms=600000)
    db.open()
    s3 = boto3.client("s3", region_name=settings.region, config=Config(signature_version="s3v4"))
    sqs = boto3.client("sqs", region_name=settings.region)
    worker = QueueWorker(db, Storage(s3, sqs, settings.bucket, settings.stream_id), settings, sqs)
    if settings.multicurve_enabled or settings.multicurve_canary_keys:
        from .multicurve.archive import Archive
        from .multicurve.pipeline import Pipeline
        from .service import Service
        archive = Archive(worker.storage)
        if settings.point_index_mode == "compact":
            from .multicurve.pointindex import CompactPointIndex
            point_index = CompactPointIndex(archive)
        else:
            from .multicurve.index import PointIndex
            client = boto3.client("dynamodb", region_name=settings.region, config=Config(
                connect_timeout=5, read_timeout=20, max_pool_connections=32,
                retries={"total_max_attempts": 1}))
            point_index = PointIndex(client, settings.point_index_table)
            point_index.bind_generation(db)
        worker.multicurve = Pipeline(Service(db, worker.storage, settings), db,
            archive, settings, point_index)
    merger = None
    if getattr(worker, "multicurve", None) and settings.point_index_mode == "compact":
        from .multicurve.pointindex import Merger
        merger = Merger(worker.multicurve, worker.multicurve.point_index)
    try:
        if args.mode == "bridge":
            from .continuations import Continuations
            jobs = Continuations(worker)
            jobs.enqueue(args.limit, args.target_weight)
            for _ in range(args.limit):
                print(json.dumps(jobs.run_one(max_extra_steps=args.max_extra_steps,
                    timeout_seconds=args.timeout_seconds, target_weight=args.target_weight)), flush=True)
        elif args.mode == "merge":
            if merger is None:
                raise SystemExit("merge needs POINT_INDEX_MODE=compact and multicurve enabled")
            for result in merger.merge_all():
                print(json.dumps({"kind": "point-index-merge", "namespace_id": result["namespace_id"],
                                  "generation": result["generation"], "entries": result["entries"],
                                  "matches": result["matches"]}), flush=True)
        elif args.mode == "reconcile":
            merge_at = 0.0
            while True:
                n = worker.repair_outbox()
                if getattr(worker, "multicurve", None):
                    n += worker.multicurve.repair()
                if merger is not None and time.monotonic() >= merge_at:
                    merge_at = time.monotonic() + settings.point_index_merge_seconds
                    try:
                        for result in merger.merge_all():
                            print(json.dumps({"kind": "point-index-merge",
                                              "namespace_id": result["namespace_id"],
                                              "generation": result["generation"],
                                              "entries": result["entries"],
                                              "matches": result["matches"]}), flush=True)
                    except Exception as exc:
                        # The next interval retries; merges are idempotent.
                        print(json.dumps({"kind": "point-index-merge", "error": type(exc).__name__}), flush=True)
                time.sleep(1 if n else 30)
        else:
            worker.poll(args.mode)
    finally:
        db.close()


if __name__ == "__main__":
    main()
