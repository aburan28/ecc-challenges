"""SQLite storage. One connection, serialised by a lock; WAL mode.

All integers that can exceed 63 bits (coordinates, scalars) are stored as
lowercase hex text.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_id            TEXT PRIMARY KEY,
    secret_hash       TEXT NOT NULL,
    label             TEXT NOT NULL,
    contact           TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('active','suspended','revoked')),
    status_reason     TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    status_changed_at REAL NOT NULL,
    last_used_at      REAL,
    shares_per_minute REAL NOT NULL,
    share_burst       INTEGER NOT NULL,
    requests_per_minute REAL NOT NULL,
    strikes           INTEGER NOT NULL DEFAULT 0,
    accepted_shares   INTEGER NOT NULL DEFAULT 0,
    audits_passed     INTEGER NOT NULL DEFAULT 0,
    audits_failed     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active','paused','solved')),
    spec          TEXT NOT NULL,          -- JSON: curve, Q, salt, dp_bits, r, ...
    created_at    REAL NOT NULL,
    solved_at     REAL,
    solution      TEXT,                   -- hex log_G(Q)
    solved_by_share INTEGER
);

CREATE TABLE IF NOT EXISTS shares (
    share_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES jobs(job_id),
    key_id       TEXT NOT NULL REFERENCES api_keys(key_id),
    nonce        TEXT NOT NULL,
    length       INTEGER NOT NULL,
    dp_x         TEXT NOT NULL,
    dp_y         TEXT NOT NULL,
    a            TEXT NOT NULL,
    b            TEXT NOT NULL,
    checkpoints  TEXT NOT NULL,           -- JSON [[x,y],...] hex
    credit       REAL NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('accepted','verified','forfeited')),
    submitted_at REAL NOT NULL,
    UNIQUE (job_id, key_id, nonce)
);
CREATE INDEX IF NOT EXISTS shares_key ON shares(key_id, status);

CREATE TABLE IF NOT EXISTS dps (
    job_id   TEXT NOT NULL,
    dp_x     TEXT NOT NULL,
    dp_y     TEXT NOT NULL,
    a        TEXT NOT NULL,
    b        TEXT NOT NULL,
    share_id INTEGER NOT NULL,
    key_id   TEXT NOT NULL,
    PRIMARY KEY (job_id, dp_x, dp_y)
);

CREATE TABLE IF NOT EXISTS collisions (
    collision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    share_a      INTEGER NOT NULL,
    share_b      INTEGER NOT NULL,
    useful       INTEGER NOT NULL,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
    audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id    INTEGER NOT NULL REFERENCES shares(share_id),
    key_id      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('segment','full')),
    segment     INTEGER,
    status      TEXT NOT NULL CHECK (status IN ('pending','passed','failed','skipped')),
    detail      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS audits_pending ON audits(status, audit_id);

CREATE TABLE IF NOT EXISTS events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    key_id     TEXT,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_key ON events(key_id, event_id);
"""


class DB:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def event(self, conn: sqlite3.Connection, key_id: str | None, kind: str,
              detail: Any) -> None:
        conn.execute(
            "INSERT INTO events (at, key_id, kind, detail) VALUES (?,?,?,?)",
            (time.time(), key_id, kind, json.dumps(detail, sort_keys=True)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
