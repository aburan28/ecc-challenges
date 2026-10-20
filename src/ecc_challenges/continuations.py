"""Bounded operator jobs that continue selected DP34 sigma trails to DP32/30.

These are derived witnesses, not new uploads or historical-point counts. A job
keeps its source batch and seed and cannot affect the contributor's checkpoint.
Only an independently checked scalar is reported as a verified solution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from .protocol import CAMPAIGN, WALK_ID, check_records


class Continuations:
    def __init__(self, worker):
        self.worker = worker
        self.db, self.storage, self.settings = worker.db, worker.storage, worker.settings

    def enqueue(self, limit: int, target_weight: int) -> int:
        if not 1 <= limit <= 100 or target_weight not in (30, 32):
            raise ValueError("invalid continuation budget")
        with self.db.connection() as conn:
            rows = conn.execute("""
                INSERT INTO ecc_public.sigma_continuations(batch_id,record_index,target_weight)
                SELECT b.id,eligible.record_index,%s FROM ecc_public.batches b
                JOIN ecc_public.runs r ON r.run_id=b.run_id
                CROSS JOIN LATERAL (
                    SELECT idx AS record_index FROM generate_series(0,b.records-1) idx
                    WHERE NOT EXISTS (SELECT 1 FROM ecc_public.sigma_continuations j
                        WHERE j.batch_id=b.id AND j.record_index=idx AND j.target_weight=%s)
                    ORDER BY idx LIMIT %s
                ) eligible
                WHERE r.dp_weight=34 AND r.trusted_at IS NOT NULL AND r.suspended_at IS NULL
                  AND b.indexed_at IS NOT NULL AND b.audit_state IN ('sampled','not_selected')
                ORDER BY b.id,eligible.record_index LIMIT %s ON CONFLICT DO NOTHING RETURNING id
            """, (target_weight, target_weight, limit, limit)).fetchall()
        return len(rows)

    def run_one(self, *, max_extra_steps: int, timeout_seconds: int, target_weight: int) -> dict:
        if not 0 <= max_extra_steps <= 2**32 or not 1 <= timeout_seconds <= 3600 or target_weight not in (30, 32):
            raise ValueError("invalid continuation budget")
        token = uuid4()
        with self.db.connection() as conn:
            job = conn.execute("""
                UPDATE ecc_public.sigma_continuations SET state='running',
                    lease_until=now()+interval '2 hours',lease_token=%s
                WHERE id=(SELECT id FROM ecc_public.sigma_continuations
                          WHERE target_weight=%s AND (state='pending' OR
                              (state='running' AND lease_until<now()))
                          ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *
            """, (token, target_weight)).fetchone()
            if job is None:
                return {"state": "idle"}
            batch = conn.execute("""
                SELECT b.*,r.dp_weight,r.trusted_at,r.suspended_at FROM ecc_public.batches b
                JOIN ecc_public.runs r ON r.run_id=b.run_id WHERE b.id=%s
            """, (job["batch_id"],)).fetchone()
        receipt = {"id": job["id"], "walk_id": WALK_ID, "batch_id": job["batch_id"],
                   "record_index": job["record_index"], "target_weight": target_weight,
                   "max_extra_steps": max_extra_steps, "timeout_seconds": timeout_seconds,
                   "state": "unverified", "matches": []}
        deadline = time.monotonic() + timeout_seconds

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise subprocess.TimeoutExpired("continuation budget", timeout_seconds)
            return seconds

        try:
            if (batch is None or batch["dp_weight"] != 34 or batch["trusted_at"] is None
                    or batch["suspended_at"] is not None or batch["indexed_at"] is None
                    or batch["audit_state"] not in ("sampled", "not_selected")):
                receipt["state"] = "discarded"
            else:
                body = self.storage.get(batch["object_key"])
                if len(body) != batch["byte_length"] or hashlib.sha256(body).hexdigest() != batch["sha256"]:
                    raise RuntimeError("source batch digest mismatch")
                check_records(body, batch["run_id"], 34)
                index = job["record_index"]
                if not 0 <= index < batch["records"]:
                    raise RuntimeError("source record is outside the batch")
                original = body[index * 32:(index + 1) * 32]
                receipt.update(source_sha256=batch["sha256"], source_record_sha256=hashlib.sha256(original).hexdigest(),
                               source_dp_weight=34, source_key=batch["object_key"],
                               replay_binary_sha256=self.worker.verifier_sha)
                # Independent arithmetic checks the original first endpoint.
                replay = subprocess.run([self.settings.replay_binary, "--dp-weight", "34"],
                                        input=original, capture_output=True, timeout=remaining())
                if replay.returncode != 0:
                    raise RuntimeError("source endpoint replay did not verify")
                binary = Path(self.settings.compatibility_binary)
                receipt["compatibility_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
                result = subprocess.run([str(binary), "continue", str(target_weight), str(max_extra_steps)],
                                        input=original, capture_output=True, timeout=remaining())
                receipt["returncode"] = result.returncode
                if result.returncode == 3:
                    receipt["state"] = "bounded"
                elif result.returncode != 0:
                    raise RuntimeError("continuation source did not verify")
                else:
                    output = json.loads(result.stdout)
                    extended = bytes.fromhex(output["record"])
                    if len(extended) != 32 or extended[:8] != original[:8] or output.get("target_weight") != target_weight:
                        raise RuntimeError("continuation changed its seed or threshold")
                    check_records(extended, batch["run_id"], target_weight)
                    replay = subprocess.run([self.settings.replay_binary, "--dp-weight", str(target_weight)],
                                            input=extended, capture_output=True, timeout=remaining())
                    if replay.returncode != 0:
                        raise RuntimeError("extended endpoint failed independent replay")
                    receipt["extension"] = output
                    seed = int.from_bytes(extended[:8], "little")
                    with self.db.connection() as conn:
                        matches = conn.execute("""
                            SELECT walk_seed,source_dp_weight AS dp_weight,worker_id AS source
                            FROM public.distinguished_points WHERE campaign_id=%s AND point_key=%s
                        """, (CAMPAIGN, extended[8:])).fetchall()
                        public = conn.execute("""
                            SELECT c.first_seed,r.dp_weight,c.first_batch FROM ecc_public.candidate_points c
                            JOIN ecc_public.runs r ON r.run_id=c.first_run
                            WHERE c.campaign_id=%s AND c.point_key=%s AND c.disqualified_at IS NULL
                              AND r.trusted_at IS NOT NULL AND r.suspended_at IS NULL
                        """, (CAMPAIGN, extended[8:])).fetchall()
                    for row in public:
                        value = int(row["first_seed"])
                        matches.append({"walk_seed": value.to_bytes(8, "big"), "dp_weight": row["dp_weight"],
                                        "source": f"public-batch:{row['first_batch']}"})
                    for match in matches:
                        old_seed = bytes(match["walk_seed"] or b"")
                        if not 1 <= len(old_seed) <= 8 or int.from_bytes(old_seed, "big") == seed:
                            continue
                        prior = int.from_bytes(old_seed, "big").to_bytes(8, "little") + extended[8:]
                        solved = self.worker.solve_compatibility(extended, prior, target_weight,
                                                                match["dp_weight"], remaining())
                        receipt["matches"].append({"source": match["source"], "prior_seed": old_seed.hex(),
                                                   "prior_dp_weight": match["dp_weight"], "solver": solved})
                    receipt["state"] = "complete"
        except subprocess.TimeoutExpired:
            receipt["state"] = "bounded"
            receipt["error"] = "wall_clock_budget"
        except Exception as exc:
            receipt["error"] = type(exc).__name__
        encoded = json.dumps(receipt, sort_keys=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        key = f"public-gateway/{self.storage.stream_id}/sigma-continuations/{job['id']}/{digest}.json"
        self.storage.put_immutable(key, encoded)
        with self.db.connection() as conn:
            changed = conn.execute("""
                UPDATE ecc_public.sigma_continuations SET state=%s,receipt=%s::jsonb,
                    lease_until=NULL,lease_token=NULL,finished_at=now()
                WHERE id=%s AND lease_token=%s RETURNING id
            """, (receipt["state"], json.dumps({**receipt, "archive_key": key}), job["id"], token)).fetchone()
        return {"id": job["id"], "state": receipt["state"] if changed else "lease_lost",
                "verified_solutions": sum(m["solver"]["verified"] for m in receipt["matches"]), "receipt": key}
