"""Small, cached public status snapshot from the isolated gateway database."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from threading import Lock
from time import monotonic

from psycopg import errors

from .catalog import CHALLENGES
from .protocol import CAMPAIGN, DP_WEIGHT, RECORD_BYTES

# Points per hour is averaged over this window: workers upload in batches of
# up to 32,768 points, so a one-hour window is mostly empty on slow cards.
RATE_WINDOW_HOURS = 24


def progress(points_by_dp: dict[int, int], rate_by_dp: dict[int, int], expected: str | None) -> dict:
    """Expected group operations behind the archived points, against Certicom's
    expected total. Each distinguished point stands for 2^dp_bits walk steps
    on average; it is an estimate of work, not a count of verified steps."""
    work = float(sum(points << bits for bits, points in points_by_dp.items()))
    ops_per_hour = float(sum(points << bits for bits, points in rate_by_dp.items())) / RATE_WINDOW_HOURS
    total = float(expected) if expected else None
    fraction = work / total if total else None
    return {
        "dp_bits": sorted(points_by_dp) or None,
        "estimated_work_operations": work,
        "estimated_work_log2": round(math.log2(work), 2) if work else None,
        "expected_operations_log2": round(math.log2(total), 2) if total else None,
        "expected_work_percent": 100 * fraction if fraction is not None else None,
        # Rho with expected cost E = sqrt(pi N / 2): P(collision by W) = 1 - exp(-pi W^2 / 4E^2).
        "estimated_completion_probability_percent":
            100 * -math.expm1(-math.pi / 4 * fraction ** 2) if fraction is not None else None,
        "points_per_hour": round(sum(rate_by_dp.values()) / RATE_WINDOW_HOURS, 1),
        "operations_per_second_log2": round(math.log2(ops_per_hour / 3600), 2) if ops_per_hour else None,
        "hours_to_expected_work": (max(total - work, 0.0) / ops_per_hour) if total and ops_per_hour else None,
    }

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
                legacy_rate = conn.execute("""
                    SELECT COALESCE(sum(records), 0) AS points
                    FROM ecc_public.batches
                    WHERE created_at >= now() - make_interval(hours => %s)
                      AND audit_state NOT IN ('failed','quarantined')
                """, (RATE_WINDOW_HOURS,)).fetchone()
                historical = _historical_totals(conn)
                generic = {}
                work = {}
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
                    # Runs stay pinned to the DP setting they registered under,
                    # so work is summed per namespace with that namespace's weight.
                    for row in conn.execute("""
                        SELECT n.campaign_id, (n.manifest->'walk'->>'dp_bits')::int AS dp_bits,
                          coalesce(sum(s.admitted_records),0) AS points,
                          (SELECT coalesce(sum(b.records),0) FROM ecc_public.dp_batches b
                           WHERE b.namespace_id=n.id AND b.state IN ('admitted','verified','indexed')
                             AND b.created_at >= now() - make_interval(hours => %s)) AS recent
                        FROM ecc_public.dp_namespaces n
                        LEFT JOIN ecc_public.dp_namespace_stats s ON s.namespace_id=n.id
                        GROUP BY n.id
                    """, (RATE_WINDOW_HOURS,)).fetchall():
                        totals_by_dp, rate_by_dp = work.setdefault(row["campaign_id"], ({}, {}))
                        bits = int(row["dp_bits"])
                        totals_by_dp[bits] = totals_by_dp.get(bits, 0) + int(row["points"])
                        rate_by_dp[bits] = rate_by_dp.get(bits, 0) + int(row["recent"])
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
                points_by_dp, rate_by_dp = (dict(d) for d in work.get(challenge.id, ({}, {})))
                # Imported from the earlier private run (same DP32 walk); not in received_points.
                historical_points = int(historical["points"]) if legacy and historical else 0
                if legacy:
                    live = int(runs["bytes"]) // RECORD_BYTES
                    points_by_dp[DP_WEIGHT] = points_by_dp.get(DP_WEIGHT, 0) + live + historical_points
                    rate_by_dp[DP_WEIGHT] = rate_by_dp.get(DP_WEIGHT, 0) + int(legacy_rate["points"])
                estimate = progress(points_by_dp, rate_by_dp, challenge.estimated_operations_2009) if enabled else {
                    "expected_operations_log2": round(math.log2(float(challenge.estimated_operations_2009)), 2)
                    if challenge.estimated_operations_2009 else None}
                return {**challenge.public(self.multicurve_enabled), **estimate,
                    "historical_points": historical_points if legacy and historical else None,
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
                    "generic_unique_points": int(current.get("unique_points", 0)) if self.multicurve_enabled else None}
            snapshot = {
                "as_of": datetime.now(timezone.utc).isoformat(),
                "compute_ready": 24 if self.multicurve_enabled else 1,
                "campaigns": [campaign_row(challenge) for challenge in CHALLENGES],
                "rate_window_hours": RATE_WINDOW_HOURS,
                "progress_note": (
                    "Work is estimated as archived points times 2^dp_bits, the mean walk length "
                    "between distinguished points; it is not a count of verified curve operations. "
                    "expected_work_percent compares it with Certicom's 2009 expected operation count. "
                    "Rho is a random search: estimated_completion_probability_percent is the chance "
                    "that a collision would have appeared by now, 1 - exp(-pi/4 * (work/expected)^2)."
                ),
            }
            snapshot["total_archived_points"] = sum(row["received_points"] or 0 for row in snapshot["campaigns"])
            snapshot["total_points_per_hour"] = round(sum(row.get("points_per_hour") or 0 for row in snapshot["campaigns"]), 1)
            snapshot["total_v2_verified_points"] = sum(row["generic_verified_points"] or 0 for row in snapshot["campaigns"])
            # total_archived_points stays this gateway's uploads only; the import is reported beside it.
            snapshot["total_historical_points"] = int(historical["points"]) if historical else None
            snapshot["historical_imported_at"] = (historical["last_loaded_at"].isoformat()
                                                  if historical and historical["last_loaded_at"] else None)
            self._cached, self._cached_at = snapshot, monotonic()
            return snapshot
