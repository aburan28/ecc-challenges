"""Small bounded PostgreSQL pool. Schema changes are explicit migrations."""

from __future__ import annotations

from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, dsn: str, max_size: int = 4, statement_timeout_ms: int = 30000):
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": False,
                    "options": (f"-c statement_timeout={statement_timeout_ms} "
                                "-c idle_in_transaction_session_timeout=45000")},
        )

    def open(self) -> None:
        self.pool.open(wait=True)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self):
        with self.pool.connection() as conn:
            yield conn
