"""Small, cached public status snapshot from the isolated gateway database."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from .catalog import CHALLENGES
from .protocol import CAMPAIGN, RECORD_BYTES


class StatusReader:
    def __init__(self, db, ttl_seconds: int = 60):
        self.db = db
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._cached: dict | None = None
        self._cached_at = 0.0

    def read(self) -> dict:
        with self._lock:
            if self._cached is not None and monotonic() - self._cached_at < self.ttl_seconds:
                return self._cached
            with self.db.connection() as conn:
                runs = conn.execute("""
                    SELECT count(*) AS total,
                           count(*) FILTER (
                             WHERE suspended_at IS NULL
                               AND last_seen_at >= now() - interval '24 hours'
                           ) AS recent,
                           COALESCE(sum(total_bytes), 0) AS bytes
                    FROM ecc_public.runs
                """).fetchone()
                pressure = conn.execute("""
                    SELECT COALESCE(sum(pending_batches), 0) AS pending_batches,
                           COALESCE(sum(pending_bytes), 0) AS pending_bytes
                    FROM ecc_public.pressure_shards
                """).fetchone()
            snapshot = {
                "as_of": datetime.now(timezone.utc).isoformat(),
                "campaigns": [
                    {**challenge.public(),
                     "received_points": int(runs["bytes"]) // RECORD_BYTES if challenge.id == CAMPAIGN else None,
                     "total_runs": int(runs["total"]) if challenge.id == CAMPAIGN else None,
                     "recent_runs_24h": int(runs["recent"]) if challenge.id == CAMPAIGN else None,
                     "pending_batches": int(pressure["pending_batches"]) if challenge.id == CAMPAIGN else None,
                     "pending_bytes": int(pressure["pending_bytes"]) if challenge.id == CAMPAIGN else None,
                     "estimated_completion_probability_percent": None}
                    for challenge in CHALLENGES
                ],
                "progress_note": (
                    "Accepted points are archived uploads, not verified curve operations. "
                    "A completion percentage needs independently verified work and a "
                    "campaign-specific collision model; these are not yet available."
                ),
            }
            self._cached, self._cached_at = snapshot, monotonic()
            return snapshot
