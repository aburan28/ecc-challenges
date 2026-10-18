"""Small, cached public status snapshot from the isolated gateway database."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from threading import Lock
from time import monotonic

from psycopg import errors

from .catalog import CHALLENGES
from .protocol import CAMPAIGN, RECORD_BYTES

LOG = logging.getLogger(__name__)


def _historical_totals(conn):
    """Imported ECC2K-130 points, or None when this database can't say yet.

    Runs in a savepoint so a missing view (migration 007 not applied) or grant
    (bootstrap not rerun) leaves the rest of the snapshot intact.
    """
    try:
        with conn.transaction():
            return conn.execute(
                "SELECT points,last_loaded_at FROM ecc_public.historical_import_totals").fetchone()
    except (errors.UndefinedTable, errors.InsufficientPrivilege) as exc:
        LOG.warning("historical import totals unavailable: %s", type(exc).__name__)
        return None


class StatusReader:
    def __init__(self, db, ttl_seconds: int = 60, *, multicurve_enabled: bool = False):
        self.db = db
        self.ttl_seconds = ttl_seconds
        self.multicurve_enabled = multicurve_enabled
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
                historical = _historical_totals(conn)
                generic = {}
                if self.multicurve_enabled:
                    rows = conn.execute("""
                        SELECT n.campaign_id,count(r.id) AS total,
                          count(r.id) FILTER (WHERE r.suspended_at IS NULL AND r.closed_at IS NULL
                            AND r.last_seen_at>=now()-interval '24 hours') AS recent,
                          coalesce(sum(r.total_records),0) AS received
                        FROM ecc_public.dp_namespaces n
                        LEFT JOIN ecc_public.dp_runs r ON r.namespace_id=n.id
                        GROUP BY n.campaign_id
                    """).fetchall()
                    generic = {row["campaign_id"]: dict(row) for row in rows}
                    totals = conn.execute("""
                        SELECT n.campaign_id,
                          coalesce(sum(s.admitted_records),0) AS received,
                          coalesce(sum(s.admitted_bytes),0) AS admitted_bytes,
                          coalesce(sum(s.verified_records),0) AS verified,
                          coalesce(sum(s.indexed_records),0) AS indexed,
                          coalesce(sum(s.unique_points),0) AS unique_points,
                          coalesce(sum(s.deduplicated_points),0) AS deduplicated
                        FROM ecc_public.dp_namespace_stats s
                        JOIN ecc_public.dp_namespaces n ON n.id=s.namespace_id
                        GROUP BY n.campaign_id
                    """).fetchall()
                    for row in totals:
                        generic.setdefault(row["campaign_id"], {}).update(row)
                    # The pending predicate matches dp_batches_run_pending; only
                    # the bounded outstanding pool participates in this scan.
                    pending = conn.execute("""
                        SELECT n.campaign_id,count(*) AS pending,
                          coalesce(sum(b.byte_length),0) AS pending_bytes
                        FROM ecc_public.dp_batches b
                        JOIN ecc_public.dp_namespaces n ON n.id=b.namespace_id
                        WHERE b.state IN ('reserved','admitted','verified')
                        GROUP BY n.campaign_id
                    """).fetchall()
                    for row in pending:
                        generic.setdefault(row["campaign_id"], {}).update(row)
            def campaign_row(challenge):
                legacy = challenge.id == CAMPAIGN
                enabled = legacy or self.multicurve_enabled
                current = generic.get(challenge.id, {})
                return {**challenge.public(self.multicurve_enabled),
                    "received_points": ((int(runs["bytes"]) // RECORD_BYTES if legacy else 0)
                                        + int(current.get("received", 0))) if enabled else None,
                    "total_runs": ((int(runs["total"]) if legacy else 0)
                                   + int(current.get("total", 0))) if enabled else None,
                    "recent_runs_24h": ((int(runs["recent"]) if legacy else 0)
                                        + int(current.get("recent", 0))) if enabled else None,
                    "pending_batches": ((int(pressure["pending_batches"]) if legacy else 0)
                                        + int(current.get("pending", 0))) if enabled else None,
                    "pending_bytes": ((int(pressure["pending_bytes"]) if legacy else 0)
                                      + int(current.get("pending_bytes", 0))) if enabled else None,
                    "generic_verified_points": int(current.get("verified", 0)) if self.multicurve_enabled else None,
                    "generic_unique_points": int(current.get("unique_points", 0)) if self.multicurve_enabled else None,
                    # Imported from the earlier private run; not in received_points.
                    "historical_points": int(historical["points"]) if legacy and historical else None,
                    "estimated_completion_probability_percent": None}
            snapshot = {
                "as_of": datetime.now(timezone.utc).isoformat(),
                "compute_ready": 24 if self.multicurve_enabled else 1,
                "campaigns": [campaign_row(challenge) for challenge in CHALLENGES],
                "progress_note": (
                    "Accepted points are archived uploads, not verified curve operations. "
                    "A completion percentage needs independently verified work and a "
                    "campaign-specific collision model; these are not yet available."
                ),
            }
            snapshot["total_archived_points"] = sum(row["received_points"] or 0 for row in snapshot["campaigns"])
            snapshot["total_v2_verified_points"] = sum(row["generic_verified_points"] or 0 for row in snapshot["campaigns"])
            # total_archived_points stays this gateway's uploads only; the import is reported beside it.
            snapshot["total_historical_points"] = int(historical["points"]) if historical else None
            snapshot["historical_imported_at"] = (historical["last_loaded_at"].isoformat()
                                                  if historical and historical["last_loaded_at"] else None)
            self._cached, self._cached_at = snapshot, monotonic()
            return snapshot
