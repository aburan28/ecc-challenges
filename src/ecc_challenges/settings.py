"""Fail-closed runtime configuration; credentials are never logged."""

from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import quote, urlencode, urlparse


@dataclass(frozen=True)
class Settings:
    database_url: str
    bucket: str
    audit_queue_url: str
    index_queue_url: str
    region: str
    key_pepper: str
    session_secret: str
    github_client_id: str
    github_client_secret: str
    public_origin: str
    run_id_start: int
    run_id_end: int
    stream_id: str
    replay_binary: str
    max_pending_batches: int
    max_key_bytes_per_day: int
    collision_queue_url: str = ""
    solver_binary: str = "/opt/ecc/ecc2k130-cpu"

    @classmethod
    def from_env(cls, *, web: bool = True) -> "Settings":
        get = os.environ.get
        database_url = get("DATABASE_URL", "")
        if not database_url and all(get(name) for name in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")):
            query = urlencode({"sslmode": "verify-full", "sslrootcert": get("DB_CA_PATH", "/app/rds-ca.pem")})
            database_url = (f"postgresql://{quote(get('DB_USER'), safe='')}:{quote(get('DB_PASSWORD'), safe='')}"
                            f"@{get('DB_HOST')}:5432/{quote(get('DB_NAME'), safe='')}?{query}")
        result = cls(
            database_url=database_url,
            bucket=get("ECC_BUCKET", ""),
            audit_queue_url=get("AUDIT_QUEUE_URL", ""),
            index_queue_url=get("INDEX_QUEUE_URL", ""),
            region=get("AWS_REGION", "us-west-2"),
            key_pepper=get("KEY_PEPPER", ""),
            session_secret=get("SESSION_SECRET", ""),
            github_client_id=get("GITHUB_CLIENT_ID", ""),
            github_client_secret=get("GITHUB_CLIENT_SECRET", ""),
            public_origin=get("PUBLIC_ORIGIN", "https://ecc-challenges.com"),
            run_id_start=int(get("RUN_ID_START", "50000")),
            run_id_end=int(get("RUN_ID_END", "65534")),
            stream_id=get("PUBLIC_STREAM_ID", "ecc-challenges-v1"),
            replay_binary=get("REPLAY_BINARY", "/opt/ecc/replay"),
            max_pending_batches=int(get("MAX_PENDING_BATCHES", "100000")),
            max_key_bytes_per_day=int(get("MAX_KEY_BYTES_PER_DAY", str(5 * 1024**3))),
            collision_queue_url=get("COLLISION_QUEUE_URL", ""),
            solver_binary=get("SOLVER_BINARY", "/opt/ecc/ecc2k130-cpu"),
        )
        required = ["database_url", "bucket", "audit_queue_url", "index_queue_url"]
        if web:
            required.extend(["github_client_id", "github_client_secret"])
        for name in required:
            if not getattr(result, name):
                raise ValueError(f"{name} is required")
        if web and (len(result.key_pepper) < 32 or len(result.session_secret) < 32):
            raise ValueError("KEY_PEPPER and SESSION_SECRET need at least 32 characters")
        if not (1 <= result.run_id_start <= result.run_id_end <= 65534):
            raise ValueError("invalid 16-bit run ID range")
        parsed = urlparse(result.public_origin)
        if parsed.scheme != "https" or parsed.path not in ("", "/") or parsed.query:
            raise ValueError("PUBLIC_ORIGIN must be an HTTPS origin")
        if "sslmode=disable" in result.database_url:
            raise ValueError("database TLS cannot be disabled")
        return result
