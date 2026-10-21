"""Small, cached public status snapshot from the isolated gateway database."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from threading import Lock
from time import monotonic

from psycopg import errors

from .catalog import CHALLENGES
from .multicurve.codec import DEFAULT_WALKS, WALK_V2, expected_steps_log2
from .multicurve.instances import get_instance
from .protocol import CAMPAIGN, DP_WEIGHT, RECORD_BYTES
from .work import SIGMA_DP_LOG2, Walk, progress, sigma_dp_log2, sigma_expected_log2

# Points per hour is averaged over this window: workers upload in batches of
# up to 32,768 points, so a one-hour window is mostly empty on slow cards.
RATE_WINDOW_HOURS = 24

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
                # to_jsonb keeps this read compatible with databases predating
                # migration 008, whose legacy runs all used DP32.
                sigma_work = conn.execute("""
                    SELECT coalesce((to_jsonb(r)->>'dp_weight')::int,32) AS dp_weight,
                           sum(r.total_bytes) AS bytes,
                           sum(coalesce(recent.points,0)) AS recent
                    FROM ecc_public.runs r
                    LEFT JOIN (
                        SELECT run_id, sum(records) AS points FROM ecc_public.batches
                        WHERE created_at >= now() - make_interval(hours => %s)
                          AND audit_state NOT IN ('failed','quarantined')
                        GROUP BY run_id
                    ) recent ON recent.run_id=r.run_id
                    GROUP BY 1
                """, (RATE_WINDOW_HOURS,)).fetchall()
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
                    # so each namespace counts as its own walk with its own weight.
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
                        work.setdefault(row["campaign_id"], []).append(
                            (int(row["dp_bits"]), int(row["points"]), int(row["recent"])))
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
                order = get_instance(challenge.id).n
                # The campaign's current walk; runs pinned to an older walk of the
                # same curve are counted against it too (a small overestimate of
                # the work they stand for when the newer walk halves the group).
                generic_walk = expected_steps_log2(order, DEFAULT_WALKS.get(challenge.id, WALK_V2))
                walks = [Walk(bits, points, recent, -bits, generic_walk)
                         for bits, points, recent in work.get(challenge.id, ())]
                # Imported from the earlier private run (same DP32 walk); not in received_points.
                historical_points = int(historical["points"]) if legacy and historical else 0
                sigma = []
                if legacy:
                    for group in sigma_work:
                        weight = int(group["dp_weight"])
                        sigma.append(Walk(weight, int(group["bytes"]) // RECORD_BYTES,
                                          int(group["recent"]), sigma_dp_log2(weight),
                                          sigma_expected_log2(order), "sigma"))
                # Each campaign is measured against the walk its worker runs.
                expected = sigma_expected_log2(order) if legacy else generic_walk
                estimate = progress(walks + sigma, expected, RATE_WINDOW_HOURS) if enabled else {
                    "expected_operations_log2": round(expected, 2)}
                combined = None
                if historical_points:
                    imported = Walk(DP_WEIGHT, historical_points, 0, SIGMA_DP_LOG2,
                                    sigma_expected_log2(order), "sigma")
                    combined = progress(walks + sigma + [imported], expected, RATE_WINDOW_HOURS)
                return {**challenge.public(self.multicurve_enabled), **estimate,
                    "including_historical": combined,
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
                    "Work is estimated as archived points times the mean walk length between "
                    "distinguished points, using each run's stored threshold: 2^28.41 at DP32 "
                    "on the ECC2K-130 sigma walk. DP32 is a "
                    "normal-basis Hamming weight of at most 32. A v2 walk uses 2^dp_bits. This is "
                    "not a count of verified curve operations. expected_operations_log2 is the "
                    "expected work of the campaign's default walk, sqrt(pi*n/(4*131)) for the sigma "
                    "walk and sqrt(pi*n/2) for the v2 walk; estimated_operations_2009_log2 is "
                    "Certicom's figure. expected_work_percent compares work with the former. "
                    "Top-level figures count this gateway's public runs only; including_historical "
                    "repeats them with the points imported from the earlier private ECC2K-130 run. "
                    "Rho is a random search: estimated_completion_probability_percent is the chance "
                    "that a collision would have appeared by now, 1 - exp(-pi/4 * (work/expected)^2)."
                ),
            }
            snapshot["total_archived_points"] = sum(row["received_points"] or 0 for row in snapshot["campaigns"])
            snapshot["total_points_per_hour"] = round(sum(row.get("points_per_hour") or 0 for row in snapshot["campaigns"]), 1)
            for key, rows in (("total_estimated_work_log2", snapshot["campaigns"]),
                              ("total_estimated_work_log2_including_historical",
                               [row["including_historical"] or row for row in snapshot["campaigns"]])):
                total = sum(row.get("estimated_work_operations") or 0 for row in rows)
                snapshot[key] = round(math.log2(total), 2) if total else None
            snapshot["total_v2_verified_points"] = sum(row["generic_verified_points"] or 0 for row in snapshot["campaigns"])
            # total_archived_points stays this gateway's uploads only; the import is reported beside it.
            snapshot["total_historical_points"] = int(historical["points"]) if historical else None
            snapshot["historical_imported_at"] = (historical["last_loaded_at"].isoformat()
                                                  if historical and historical["last_loaded_at"] else None)
            self._cached, self._cached_at = snapshot, monotonic()
            return snapshot
