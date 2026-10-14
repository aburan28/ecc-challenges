"""HTTP API (FastAPI).

Worker endpoints authenticate with ``Authorization: Bearer eccp_<id>_<secret>``.
Admin endpoints use ``Authorization: Bearer <ECCPOOL_ADMIN_TOKEN>``.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .curvegen import make_challenge
from .db import DB
from .ec import Curve, is_probable_prime
from .keys import KeyRecord, KeyStore
from .pool import Auditor, Pool, PoolConfig
from .ratelimit import TokenBuckets

log = logging.getLogger("eccpool")


@dataclass
class ServerConfig:
    db_path: str = "eccpool.sqlite3"
    pepper: bytes = b""
    admin_token: str = ""
    trust_proxy: bool = False
    max_body_bytes: int = 2 * 1024 * 1024
    ip_requests_per_minute: float = 600
    auth_failures_per_minute: float = 10
    auth_failure_burst: int = 20
    key_defaults: dict = field(default_factory=lambda: {
        "shares_per_minute": 600.0, "share_burst": 200, "requests_per_minute": 120.0})
    pool: PoolConfig = field(default_factory=PoolConfig)
    run_auditor: bool = True

    @classmethod
    def from_env(cls) -> "ServerConfig":
        pepper = os.environ.get("ECCPOOL_PEPPER", "")
        admin = os.environ.get("ECCPOOL_ADMIN_TOKEN", "")
        if len(pepper) < 32 or len(admin) < 32:
            raise SystemExit("set ECCPOOL_PEPPER and ECCPOOL_ADMIN_TOKEN (32+ random characters each)")
        cfg = cls(db_path=os.environ.get("ECCPOOL_DB", "eccpool.sqlite3"),
                  pepper=pepper.encode(), admin_token=admin,
                  trust_proxy=os.environ.get("ECCPOOL_TRUST_PROXY", "") == "1")
        for env, attr in (("ECCPOOL_AUDIT_RATE", "audit_rate"),
                          ("ECCPOOL_FULL_AUDIT_RATE", "full_audit_rate"),
                          ("ECCPOOL_PROBATION_AUDIT_RATE", "probation_audit_rate")):
            if env in os.environ:
                setattr(cfg.pool, attr, float(os.environ[env]))
        for env, attr in (("ECCPOOL_PROBATION_SHARES", "probation_shares"),
                          ("ECCPOOL_MAX_STRIKES", "max_strikes")):
            if env in os.environ:
                setattr(cfg.pool, attr, int(os.environ[env]))
        return cfg


class Throttled(Exception):
    def __init__(self, retry_after: float, scope: str) -> None:
        self.retry_after = retry_after
        self.scope = scope


# ------------------------------------------------------------------ models
class SubmitBody(BaseModel):
    job_id: str = Field(max_length=32)
    shares: list = Field(min_length=1)


class IssueKeyBody(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    contact: str = Field(default="", max_length=200)
    shares_per_minute: float | None = Field(default=None, ge=0)
    share_burst: int | None = Field(default=None, ge=1)
    requests_per_minute: float | None = Field(default=None, ge=1)


class StatusBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class LimitsBody(BaseModel):
    shares_per_minute: float | None = Field(default=None, ge=0)
    share_burst: int | None = Field(default=None, ge=1)
    requests_per_minute: float | None = Field(default=None, ge=1)


class AuditBody(BaseModel):
    count: int = Field(default=10, ge=1, le=10_000)
    kind: str = Field(default="segment", pattern="^(segment|full)$")


class JobBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dp_bits: int = Field(ge=1, le=40)
    r: int = Field(default=32, ge=2, le=1024)
    checkpoint_interval: int = Field(default=0, ge=0)
    max_walk_len: int = Field(default=0, ge=0)
    curve: dict
    Q: list[str] | None = None
    seed: str | None = Field(default=None, max_length=200)


class JobStatusBody(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


# --------------------------------------------------------------------- app
def create_app(cfg: ServerConfig) -> FastAPI:
    db = DB(cfg.db_path)
    keys = KeyStore(db, cfg.pepper, cfg.key_defaults)
    pool = Pool(db, keys, cfg.pool)
    buckets = TokenBuckets()
    auditor = Auditor(pool)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.run_auditor:
            auditor.start()
        yield
        auditor.stop()

    app = FastAPI(title="ECC challenges pool", version="1.0", lifespan=lifespan)
    app.state.pool = pool
    app.state.keys = keys
    app.state.db = db
    app.state.buckets = buckets

    def client_ip(request: Request) -> str:
        if cfg.trust_proxy:
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def throttle(bucket: str, rate: float, burst: float, cost: float = 1.0) -> None:
        wait = buckets.take(bucket, rate, burst, cost)
        if wait:
            raise Throttled(wait, bucket.split(":", 1)[0])

    def bearer(request: Request) -> str:
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            return h[7:].strip()
        return request.headers.get("x-api-key", "").strip()

    def auth_failed(ip: str, reason: str, status: int = 401) -> HTTPException:
        # Failed authentication spends from a small per-IP bucket; brute force
        # and hammering with a revoked key both run dry quickly.
        wait = buckets.take(f"authfail:{ip}", cfg.auth_failures_per_minute, cfg.auth_failure_burst)
        if wait:
            raise Throttled(wait, "authfail")
        return HTTPException(status, reason)

    async def require_key(request: Request) -> KeyRecord:
        ip = client_ip(request)
        throttle(f"ip:{ip}", cfg.ip_requests_per_minute, cfg.ip_requests_per_minute / 4)
        rec, reason = keys.authenticate(bearer(request))
        if rec is None:
            raise auth_failed(ip, reason, 403 if "suspended" in reason or "revoked" in reason else 401)
        throttle(f"req:{rec.key_id}", rec.requests_per_minute, max(10.0, rec.requests_per_minute / 6))
        return rec

    async def require_admin(request: Request) -> None:
        ip = client_ip(request)
        throttle(f"ip:{ip}", cfg.ip_requests_per_minute, cfg.ip_requests_per_minute / 4)
        if not cfg.admin_token or not hmac.compare_digest(bearer(request).encode(),
                                                          cfg.admin_token.encode()):
            raise auth_failed(ip, "admin token required")

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > cfg.max_body_bytes):
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        if cl is None and request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            if len(body) > cfg.max_body_bytes:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(Throttled)
    async def throttled(request: Request, exc: Throttled):
        retry = 3600 if math.isinf(exc.retry_after) else max(1, math.ceil(exc.retry_after))
        return JSONResponse({"detail": f"rate limited ({exc.scope})", "retry_after": retry},
                            status_code=429, headers={"Retry-After": str(retry)})

    # ------------------------------------------------------------ public
    @app.get("/healthz")
    def healthz():
        db.one("SELECT 1")
        return {"ok": True}

    # ------------------------------------------------------------ worker
    @app.get("/v1/jobs")
    def list_jobs(key: KeyRecord = Depends(require_key)):
        rows = db.query("SELECT job_id FROM jobs ORDER BY created_at")
        return {"jobs": [pool.job_public(pool.job(r["job_id"])) for r in rows]}

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, key: KeyRecord = Depends(require_key)):
        job = pool.job(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return pool.job_public(job)

    @app.get("/v1/me")
    def me(key: KeyRecord = Depends(require_key)):
        return pool.key_stats(key.key_id)

    @app.post("/v1/shares")
    def submit(body: SubmitBody, key: KeyRecord = Depends(require_key)):
        if len(body.shares) > cfg.pool.max_batch:
            raise HTTPException(413, f"at most {cfg.pool.max_batch} shares per request")
        if len(body.shares) > key.share_burst:
            raise HTTPException(413, f"batch of {len(body.shares)} exceeds this key's share burst "
                                     f"({key.share_burst}); send smaller batches")
        throttle(f"shares:{key.key_id}", key.shares_per_minute, key.share_burst, len(body.shares))
        try:
            outcomes = pool.submit(key.key_id, body.job_id, body.shares)
        except LookupError:
            raise HTTPException(404, "unknown job") from None
        result = {"results": [o.as_dict() for o in outcomes]}
        if len(outcomes) < len(body.shares):
            result["detail"] = "batch stopped: key is no longer active"
        return result

    # ------------------------------------------------------------- admin
    admin = [Depends(require_admin)]

    @app.post("/admin/keys", dependencies=admin, status_code=201)
    def issue_key(body: IssueKeyBody):
        key_id, token = keys.issue(body.label, body.contact, body.shares_per_minute,
                                   body.share_burst, body.requests_per_minute)
        return {"key_id": key_id, "api_key": token,
                "note": "store this key now; the server keeps only a hash"}

    @app.get("/admin/keys", dependencies=admin)
    def list_keys(status: str | None = None):
        sql = "SELECT key_id FROM api_keys"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        return {"keys": [pool.key_stats(r["key_id"]) for r in db.query(sql + " ORDER BY created_at", args)]}

    @app.get("/admin/keys/{key_id}", dependencies=admin)
    def get_key(key_id: str):
        if keys.get(key_id) is None:
            raise HTTPException(404, "unknown key")
        stats = pool.key_stats(key_id)
        stats["events"] = [dict(r) for r in db.query(
            "SELECT at, kind, detail FROM events WHERE key_id = ? ORDER BY event_id DESC LIMIT 50",
            (key_id,))]
        for e in stats["events"]:
            e["detail"] = json.loads(e["detail"])
        return stats

    def _status(key_id: str, status: str, reason: str):
        try:
            ok = keys.set_status(key_id, status, reason)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        if not ok:
            raise HTTPException(404, "unknown key")
        return pool.key_stats(key_id)

    @app.post("/admin/keys/{key_id}/revoke", dependencies=admin)
    def revoke(key_id: str, body: StatusBody, forfeit: bool = False):
        if forfeit:
            if keys.get(key_id) is None:
                raise HTTPException(404, "unknown key")
            with db.tx() as c:
                pool.forfeit(c, key_id, body.reason)
            return pool.key_stats(key_id)
        return _status(key_id, "revoked", body.reason)

    @app.post("/admin/keys/{key_id}/suspend", dependencies=admin)
    def suspend(key_id: str, body: StatusBody):
        return _status(key_id, "suspended", body.reason)

    @app.post("/admin/keys/{key_id}/reinstate", dependencies=admin)
    def reinstate(key_id: str, body: StatusBody):
        out = _status(key_id, "active", body.reason)
        with db.tx() as c:
            c.execute("UPDATE api_keys SET strikes = 0 WHERE key_id = ?", (key_id,))
        out["strikes"] = 0
        return out

    @app.patch("/admin/keys/{key_id}/limits", dependencies=admin)
    def limits(key_id: str, body: LimitsBody):
        if not keys.update_limits(key_id, **body.model_dump()):
            raise HTTPException(404, "unknown key")
        return pool.key_stats(key_id)

    @app.post("/admin/keys/{key_id}/audit", dependencies=admin)
    def audit_key(key_id: str, body: AuditBody):
        if keys.get(key_id) is None:
            raise HTTPException(404, "unknown key")
        return {"queued": pool.queue_audits_for_key(key_id, body.count, body.kind)}

    @app.post("/admin/jobs", dependencies=admin, status_code=201)
    def create_job(body: JobBody):
        try:
            curve = Curve.from_dict(body.curve)
            if body.Q is None:
                if curve.p < 5 or not is_probable_prime(curve.p):
                    raise ValueError("p must be prime")
                if not body.seed:
                    raise ValueError("give Q or a seed to hash-derive G and Q")
                ch = make_challenge(curve, body.seed)
                curve = Curve.from_dict(ch["curve"])
                Q = (int(ch["Q"][0], 16), int(ch["Q"][1], 16))
            else:
                Q = (int(body.Q[0], 16), int(body.Q[1], 16))
            job_id = pool.create_job(body.name, curve, Q, body.dp_bits, body.r,
                                     body.checkpoint_interval, body.max_walk_len)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e)) from None
        return pool.job_public(pool.job(job_id))

    @app.post("/admin/jobs/{job_id}/status", dependencies=admin)
    def job_status(job_id: str, body: JobStatusBody):
        if not pool.set_job_status(job_id, body.status):
            raise HTTPException(409, "unknown or solved job")
        return pool.job_public(pool.job(job_id))

    @app.get("/admin/audits", dependencies=admin)
    def audits(status: str | None = None, limit: int = 100):
        sql, args = "SELECT * FROM audits", ()
        if status:
            sql, args = sql + " WHERE status = ?", (status,)
        return {"audits": [dict(r) for r in db.query(sql + " ORDER BY audit_id DESC LIMIT ?",
                                                     (*args, min(limit, 1000)))]}

    @app.get("/admin/events", dependencies=admin)
    def events(key_id: str | None = None, limit: int = 100):
        sql, args = "SELECT * FROM events", ()
        if key_id:
            sql, args = sql + " WHERE key_id = ?", (key_id,)
        rows = [dict(r) for r in db.query(sql + " ORDER BY event_id DESC LIMIT ?",
                                          (*args, min(limit, 1000)))]
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return {"events": rows}

    @app.get("/admin/collisions", dependencies=admin)
    def collisions(job_id: str | None = None):
        sql, args = "SELECT * FROM collisions", ()
        if job_id:
            sql, args = sql + " WHERE job_id = ?", (job_id,)
        return {"collisions": [dict(r) for r in db.query(sql + " ORDER BY collision_id", args)]}

    return app


def app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app(ServerConfig.from_env())
