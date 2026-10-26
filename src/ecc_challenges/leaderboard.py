"""The public leaderboard: estimated curve operations per contributor.

Points are not comparable across curves or thresholds, so each run's points
are converted to estimated rho steps the same way the status page does: a
sigma point at weight w stands for 2^-sigma_dp_log2(w) steps and a protocol 2
point with t distinguishing bits for 2^t. Work on solved challenges (test runs
only), suspended runs and suspended accounts is left out, as are points in
batches that failed their checks.

Only accounts that opted in are named. Everyone else is folded into one row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import log2
from threading import Lock
from time import monotonic

from .catalog import BY_ID
from .protocol import CAMPAIGN
from .work import sigma_dp_log2

LIMIT = 100


@dataclass
class Contribution:
    steps: float = 0.0
    points: int = 0
    runs: int = 0
    challenges: set[str] = field(default_factory=set)
    last_seen_at: datetime | None = None

    def add(self, challenge: str, points: int, steps_per_point: float, runs: int,
            last_seen_at: datetime | None) -> None:
        if points <= 0:
            return
        self.steps += points * steps_per_point
        self.points += points
        self.runs += runs
        self.challenges.add(challenge)
        if last_seen_at and (self.last_seen_at is None or last_seen_at > self.last_seen_at):
            self.last_seen_at = last_seen_at


def counts_toward_leaderboard(challenge: str) -> bool:
    challenge_row = BY_ID.get(challenge)
    return challenge_row is not None and not challenge_row.solved_in_2009


def rank(accounts: dict[int, dict], sigma_rows, generic_rows, limit: int = LIMIT) -> dict:
    """Combine per-run work into a ranked table; pure so it can be tested directly.

    accounts maps account id to {"login", "visible", "suspended"}. sigma_rows
    hold (account_id, dp_weight, points, runs, last_seen_at) for protocol 1;
    generic_rows hold (account_id, challenge, dp_bits, points, runs, last_seen_at).
    """
    work: dict[int, Contribution] = {}
    for account_id, weight, points, runs, seen in sigma_rows:
        work.setdefault(account_id, Contribution()).add(
            CAMPAIGN, int(points), 2.0 ** -sigma_dp_log2(int(weight)), int(runs), seen)
    for account_id, challenge, dp_bits, points, runs, seen in generic_rows:
        if counts_toward_leaderboard(challenge):
            work.setdefault(account_id, Contribution()).add(
                challenge, int(points), 2.0 ** int(dp_bits), int(runs), seen)
    named, hidden = [], Contribution()
    hidden_accounts = 0
    for account_id, total in work.items():
        account = accounts.get(account_id)
        if account is None or account["suspended"] or total.points == 0:
            continue
        if account["visible"]:
            named.append((account["login"], total))
        else:
            hidden_accounts += 1
            hidden.steps += total.steps
            hidden.points += total.points
    named.sort(key=lambda item: (-item[1].steps, item[0].lower()))
    entries = [{
        "rank": index + 1,
        "login": login,
        "operations_log2": round(log2(total.steps), 2),
        "points": total.points,
        "runs": total.runs,
        "challenges": sorted(total.challenges, key=lambda c: (BY_ID[c].field_bits, c)),
        "last_seen_at": total.last_seen_at,
    } for index, (login, total) in enumerate(named[:limit])]
    every = sum(total.steps for _, total in named) + hidden.steps
    return {
        "entries": entries,
        "named_total": len(named),
        "hidden": {"accounts": hidden_accounts,
                   "operations_log2": round(log2(hidden.steps), 2) if hidden.steps else None,
                   "points": hidden.points},
        "total_operations_log2": round(log2(every), 2) if every else None,
    }


class LeaderboardReader:
    def __init__(self, db, ttl_seconds: int = 300, *, multicurve_enabled: bool = False):
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
                # to_jsonb keeps these reads working before migrations 008 and
                # 012 have been applied: legacy runs used DP32, and accounts
                # without the column are hidden.
                accounts = {row["github_id"]: {
                    "login": row["login"], "visible": bool(row["visible"]),
                    "suspended": row["suspended_at"] is not None,
                } for row in conn.execute("""
                    SELECT a.github_id, a.login, a.suspended_at,
                           coalesce((to_jsonb(a)->>'leaderboard_visible')::boolean, false) AS visible
                    FROM ecc_public.accounts a
                """).fetchall()}
                sigma = conn.execute("""
                    SELECT r.account_id,
                           coalesce((to_jsonb(r)->>'dp_weight')::int, 32) AS dp_weight,
                           sum(r.total_bytes / 32 - coalesce(f.records, 0)) AS points,
                           count(*) AS runs, max(r.last_seen_at) AS last_seen_at
                    FROM ecc_public.runs r
                    LEFT JOIN (
                        SELECT run_id, sum(records) AS records FROM ecc_public.batches
                        WHERE audit_state IN ('failed', 'quarantined') GROUP BY run_id
                    ) f ON f.run_id = r.run_id
                    WHERE r.suspended_at IS NULL
                    GROUP BY 1, 2
                """).fetchall()
                generic = []
                if self.multicurve_enabled:
                    generic = conn.execute("""
                        SELECT r.account_id, n.campaign_id,
                               (n.manifest->'walk'->>'dp_bits')::int AS dp_bits,
                               sum(r.total_records - coalesce(f.records, 0)) AS points,
                               count(*) AS runs, max(r.last_seen_at) AS last_seen_at
                        FROM ecc_public.dp_runs r
                        JOIN ecc_public.dp_namespaces n ON n.id = r.namespace_id
                        LEFT JOIN (
                            SELECT run_id, sum(records) AS records FROM ecc_public.dp_batches
                            WHERE state = 'failed' GROUP BY run_id
                        ) f ON f.run_id = r.id
                        WHERE r.suspended_at IS NULL
                        GROUP BY 1, 2, 3
                    """).fetchall()
            result = rank(
                accounts,
                [(r["account_id"], r["dp_weight"], r["points"], r["runs"], r["last_seen_at"]) for r in sigma],
                [(r["account_id"], r["campaign_id"], r["dp_bits"], r["points"], r["runs"], r["last_seen_at"])
                 for r in generic])
            self._cached, self._cached_at = result, monotonic()
            return result

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
