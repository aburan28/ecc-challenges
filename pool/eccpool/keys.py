"""API keys: issue, authenticate, change status.

A key looks like ``eccp_<key_id>_<secret>``. Only HMAC-SHA256(pepper, secret)
is stored, so a database leak does not leak usable keys; the pepper lives in
the server's environment, not the database.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from .db import DB

PREFIX = "eccp"
STATUSES = ("active", "suspended", "revoked")


@dataclass
class KeyRecord:
    key_id: str
    label: str
    status: str
    shares_per_minute: float
    share_burst: int
    requests_per_minute: float
    accepted_shares: int
    strikes: int


def _hash_secret(pepper: bytes, secret: str) -> str:
    return hmac.new(pepper, secret.encode(), hashlib.sha256).hexdigest()


def parse_key(token: str) -> tuple[str, str] | None:
    parts = token.strip().split("_")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        return None
    if len(token) > 128:
        return None
    return parts[1], parts[2]


class KeyStore:
    def __init__(self, db: DB, pepper: bytes, defaults: dict) -> None:
        self.db = db
        self.pepper = pepper
        self.defaults = defaults

    def issue(self, label: str, contact: str = "", shares_per_minute: float | None = None,
              share_burst: int | None = None,
              requests_per_minute: float | None = None) -> tuple[str, str]:
        """Create a key; returns (key_id, full token). The token is shown once."""
        key_id = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32).replace("_", "-")
        now = time.time()
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO api_keys (key_id, secret_hash, label, contact, status,
                   created_at, status_changed_at, shares_per_minute, share_burst,
                   requests_per_minute) VALUES (?,?,?,?, 'active', ?,?,?,?,?)""",
                (key_id, _hash_secret(self.pepper, secret), label, contact, now, now,
                 shares_per_minute if shares_per_minute is not None else self.defaults["shares_per_minute"],
                 share_burst if share_burst is not None else self.defaults["share_burst"],
                 requests_per_minute if requests_per_minute is not None else self.defaults["requests_per_minute"]),
            )
            self.db.event(c, key_id, "key_issued", {"label": label})
        return key_id, f"{PREFIX}_{key_id}_{secret}"

    def authenticate(self, token: str) -> tuple[KeyRecord | None, str]:
        """Returns (record, '') on success or (None, reason)."""
        parsed = parse_key(token)
        if parsed is None:
            return None, "malformed API key"
        key_id, secret = parsed
        row = self.db.one("SELECT * FROM api_keys WHERE key_id = ?", (key_id,))
        expected = row["secret_hash"] if row else _hash_secret(self.pepper, "dummy")
        ok = hmac.compare_digest(expected, _hash_secret(self.pepper, secret))
        if row is None or not ok:
            return None, "invalid API key"
        if row["status"] != "active":
            return None, f"API key {row['status']}: {row['status_reason']}"
        return self._record(row), ""

    def get(self, key_id: str) -> KeyRecord | None:
        row = self.db.one("SELECT * FROM api_keys WHERE key_id = ?", (key_id,))
        return self._record(row) if row else None

    @staticmethod
    def _record(row) -> KeyRecord:
        return KeyRecord(row["key_id"], row["label"], row["status"],
                         row["shares_per_minute"], row["share_burst"],
                         row["requests_per_minute"], row["accepted_shares"], row["strikes"])

    def set_status(self, key_id: str, status: str, reason: str, conn=None) -> bool:
        if status not in STATUSES:
            raise ValueError(status)

        def apply(c) -> bool:
            row = c.execute("SELECT status FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == "revoked" and status != "revoked":
                raise ValueError("revoked keys cannot be reinstated; issue a new key")
            c.execute("UPDATE api_keys SET status = ?, status_reason = ?, status_changed_at = ?"
                      " WHERE key_id = ?", (status, reason, time.time(), key_id))
            self.db.event(c, key_id, f"key_{status}", {"reason": reason, "from": row["status"]})
            return True

        if conn is not None:
            return apply(conn)
        with self.db.tx() as c:
            return apply(c)

    def update_limits(self, key_id: str, **limits) -> bool:
        allowed = {"shares_per_minute", "share_burst", "requests_per_minute"}
        sets = {k: v for k, v in limits.items() if k in allowed and v is not None}
        if not sets:
            return self.get(key_id) is not None
        with self.db.tx() as c:
            cur = c.execute(
                f"UPDATE api_keys SET {', '.join(f'{k} = ?' for k in sets)} WHERE key_id = ?",
                (*sets.values(), key_id))
            if cur.rowcount:
                self.db.event(c, key_id, "limits_changed", sets)
            return cur.rowcount > 0

    def touch(self, key_id: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                      (time.time(), key_id))
