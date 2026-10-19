"""Public website and stateless HTTPS worker API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets
from urllib.parse import parse_qs

from authlib.integrations.starlette_client import OAuth
import boto3
from botocore.config import Config
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .db import Database
from .catalog import BY_ID, CHALLENGES, SOURCE_DATE, SOURCE_URL
from .multicurve.instances import get_instance
from .protocol import (CAMPAIGN, WALK_ID, DP_WEIGHT, DP_WEIGHTS, RECOMMENDED_DP_WEIGHT,
                       RECORD_BYTES, MAX_BATCH_BYTES, MAX_CHECKPOINT_BYTES)
from .security import new_csrf
from .service import Service, Rejected
from .settings import Settings
from .storage import Storage
from .status import StatusReader

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["level_labels"] = {"exercise": "Exercise", "level_i": "Level I", "level_ii": "Level II"}
ERROR_TITLES = {401: "Sign in required", 403: "Not allowed", 404: "Page not found", 429: "Slow down"}
# Images the templates reference, served from the package by name only.
ASSETS = {p.name: p.read_bytes() for p in (Path(__file__).parent / "assets").glob("*.png")}
# One-line installers for the native worker: curl ... | sh and irm ... | iex.
INSTALLERS = {p.name: p.read_text() for p in (Path(__file__).parent / "install").glob("install.*")}
ROBOTS = "User-agent: *\nDisallow: /dashboard\nDisallow: /keys\nDisallow: /auth/\nDisallow: /v1/\nDisallow: /v2/\n"
LOG = logging.getLogger(__name__)


async def limited_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > limit:
                raise Rejected(413, "request body too large")
        except ValueError:
            raise Rejected(400, "invalid Content-Length") from None
    parts = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise Rejected(413, "request body too large")
        parts.append(chunk)
    return b"".join(parts)


def declared_binary_length(request: Request, limit: int) -> int:
    value = request.headers.get("content-length")
    if value is None:
        raise Rejected(411, "Content-Length is required for binary uploads")
    try:
        length = int(value)
    except ValueError:
        raise Rejected(400, "invalid Content-Length") from None
    if length < 0 or length > limit:
        raise Rejected(413, "request body too large")
    return length


def bearer_value(request: Request) -> str:
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer ") or len(value) > 160:
        raise Rejected(401, "API key required")
    return value[7:]


def client_os(request: Request) -> str:
    """Which setup tab the dashboard opens first, from the browser's platform."""
    agent = request.headers.get("user-agent", "")
    if "Windows" in agent:
        return "windows"
    if "Macintosh" in agent or "Mac OS X" in agent:
        return "macos"
    return "linux"


def require_session(request: Request) -> int:
    account_id = request.session.get("github_id")
    if not isinstance(account_id, int) or account_id <= 0:
        raise Rejected(401, "sign in to manage API keys")
    return account_id


async def form_values(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise Rejected(415, "form content type required")
    raw = await limited_body(request, 4096)
    try:
        values = parse_qs(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        raise Rejected(400, "invalid form body") from None
    if any(len(v) != 1 for v in values.values()):
        raise Rejected(400, "duplicate form value")
    return {k: v[0] for k, v in values.items()}


def require_csrf(request: Request, supplied: str | None) -> None:
    expected = request.session.get("csrf")
    if not isinstance(expected, str) or not supplied or not secrets.compare_digest(expected, supplied):
        raise Rejected(403, "invalid form token")


def create_app(settings: Settings | None = None, *, db=None, storage=None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = db or Database(settings.database_url)
    if storage is None:
        s3 = boto3.client("s3", region_name=settings.region, config=Config(signature_version="s3v4"))
        sqs = boto3.client("sqs", region_name=settings.region)
        storage = Storage(s3, sqs, settings.bucket, settings.stream_id)
    service = Service(database, storage, settings)
    multicurve_enabled = getattr(settings, "multicurve_enabled", False)
    status_reader = StatusReader(database, multicurve_enabled=multicurve_enabled)
    oauth = OAuth()
    oauth.register(
        name="github", client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
    )

    @asynccontextmanager
    async def lifespan(_app):
        if db is None:
            await run_in_threadpool(database.open)
        yield
        if db is None:
            await run_in_threadpool(database.close)

    app = FastAPI(title="ECC Challenges", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="ecc_session", https_only=True, same_site="lax", max_age=86400)
    app.state.service = service
    if multicurve_enabled or getattr(settings, "multicurve_canary_keys", ()):
        from .multicurve.api import create_router
        app.include_router(create_router(service, database, storage, settings))
    checkpoint_slots = asyncio.Semaphore(1)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; img-src 'self' data:"
        )
        return response

    @app.exception_handler(Rejected)
    async def rejected(request: Request, exc: Rejected):
        headers = {"Cache-Control": "no-store"}
        if exc.retry_after:
            headers["Retry-After"] = str(exc.retry_after)
        if request.url.path.startswith(("/v1/", "/v2/")) or request.url.path == "/health":
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=headers)
        return error_page(request, exc.status, str(exc), headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(("/v1/", "/v2/")) or request.url.path == "/health":
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        message = "there is nothing at this address" if exc.status_code == 404 else str(exc.detail).lower()
        return error_page(request, exc.status_code, message, exc.headers)

    def error_page(request: Request, status: int, message: str, headers=None):
        return TEMPLATES.TemplateResponse(request, "error.html",
                                          {"message": message, "status_code": status,
                                           "title": ERROR_TITLES.get(status)},
                                          status_code=status, headers=headers)

    @app.get("/health")
    async def health():
        def check():
            with database.connection() as conn:
                conn.execute("SELECT 1")
            return {"ok": True}
        return await run_in_threadpool(check)

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots():
        return PlainTextResponse(ROBOTS, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/assets/{name}")
    async def asset(name: str):
        if name not in ASSETS:
            raise StarletteHTTPException(404)
        return Response(ASSETS[name], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/install.sh", response_class=PlainTextResponse)
    @app.get("/install.ps1", response_class=PlainTextResponse)
    async def installer(request: Request):
        return PlainTextResponse(INSTALLERS[request.url.path.lstrip("/")],
                                 headers={"Cache-Control": "public, max-age=300"})

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        try:
            snapshot = await run_in_threadpool(status_reader.read)
        except Exception:
            LOG.exception("public status snapshot failed")
            snapshot = None
        return TEMPLATES.TemplateResponse(request, "home.html", {"signed_in": bool(request.session.get("github_id")),
                                          "challenges": CHALLENGES, "catalog_source": SOURCE_URL,
                                          "catalog_date": SOURCE_DATE, "snapshot": snapshot,
                                          "multicurve_enabled": multicurve_enabled},
                                          headers={"Cache-Control": "private, no-store"})

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request):
        try:
            snapshot = await run_in_threadpool(status_reader.read)
        except Exception:
            LOG.exception("public status snapshot failed")
            snapshot = None
        return TEMPLATES.TemplateResponse(request, "status.html",
                                          {"snapshot": snapshot, "challenges": CHALLENGES,
                                           "catalog_source": SOURCE_URL},
                                          headers={"Cache-Control": "public, max-age=30"})

    @app.get("/challenges/{campaign_id}", response_class=HTMLResponse)
    async def challenge_page(request: Request, campaign_id: str):
        challenge = BY_ID.get(campaign_id.lower())
        if challenge is None:
            raise StarletteHTTPException(404, "Challenge not found")
        try:
            snapshot = await run_in_threadpool(status_reader.read)
        except Exception:
            LOG.exception("public status snapshot failed for challenge page")
            snapshot = None
        row = (next((item for item in snapshot["campaigns"] if item["id"] == challenge.id), None)
               if snapshot else None)
        return TEMPLATES.TemplateResponse(request, "challenge.html", {
            "challenge": challenge,
            "challenges": CHALLENGES,
            "status": row,
            "parameters": get_instance(challenge.id).public(),
            "catalog_source": SOURCE_URL,
            "catalog_date": SOURCE_DATE,
            "multicurve_enabled": multicurve_enabled,
        }, headers={"Cache-Control": "private, no-store"})

    @app.get("/v1/status")
    async def status_json():
        try:
            snapshot = await run_in_threadpool(status_reader.read)
        except Exception:
            LOG.exception("public status snapshot failed")
            raise Rejected(503, "status snapshot is temporarily unavailable", 60) from None
        return JSONResponse(snapshot, headers={"Cache-Control": "public, max-age=30"})

    @app.get("/auth/github")
    async def auth_start(request: Request):
        return await oauth.github.authorize_redirect(request, settings.public_origin + "/auth/callback")

    @app.get("/auth/callback")
    async def auth_callback(request: Request):
        token = await oauth.github.authorize_access_token(request)
        response = await oauth.github.get("user", token=token)
        response.raise_for_status()
        profile = response.json()
        account_id, login = int(profile["id"]), str(profile["login"])
        await run_in_threadpool(service.upsert_account, account_id, login)
        request.session.clear()
        request.session.update({"github_id": account_id, "login": login, "csrf": new_csrf()})
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        values = await form_values(request)
        require_csrf(request, values.get("csrf"))
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, dp_weight: int = RECOMMENDED_DP_WEIGHT):
        account_id = require_session(request)
        if dp_weight not in DP_WEIGHTS:
            raise Rejected(400, "unsupported sigma reporting threshold")
        if "csrf" not in request.session:
            request.session["csrf"] = new_csrf()
        keys = await run_in_threadpool(service.account_keys, account_id)
        return TEMPLATES.TemplateResponse(request, "dashboard.html",
            {"login": request.session.get("login"), "keys": keys,
             "csrf": request.session["csrf"], "issued": None,
             "worker_image": settings.public_worker_image, "challenges": CHALLENGES,
             "cpu_worker_image": getattr(settings, "public_cpu_worker_image", ""),
             "multicurve_enabled": multicurve_enabled, "client_os": client_os(request),
             "selected_dp_weight": dp_weight, "recommended_dp_weight": RECOMMENDED_DP_WEIGHT,
             "sigma_dp_weights": sorted(DP_WEIGHTS, reverse=True),
             "selected_campaign": request.query_params.get("campaign", CAMPAIGN)
                 if multicurve_enabled and request.query_params.get("campaign", CAMPAIGN) in BY_ID else CAMPAIGN},
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    @app.post("/keys", response_class=HTMLResponse)
    async def create_key(request: Request):
        account_id = require_session(request)
        values = await form_values(request)
        require_csrf(request, values.get("csrf"))
        issued = await run_in_threadpool(service.create_key, account_id, values.get("label", ""))
        keys = await run_in_threadpool(service.account_keys, account_id)
        return TEMPLATES.TemplateResponse(request, "dashboard.html",
            {"login": request.session.get("login"), "keys": keys,
             "csrf": request.session["csrf"], "issued": issued,
             "worker_image": settings.public_worker_image, "challenges": CHALLENGES,
             "cpu_worker_image": getattr(settings, "public_cpu_worker_image", ""),
             "multicurve_enabled": multicurve_enabled, "selected_campaign": CAMPAIGN,
             "selected_dp_weight": RECOMMENDED_DP_WEIGHT, "recommended_dp_weight": RECOMMENDED_DP_WEIGHT,
             "sigma_dp_weights": sorted(DP_WEIGHTS, reverse=True),
             "client_os": client_os(request)},
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    @app.post("/keys/{key_id}/revoke")
    async def revoke_key(request: Request, key_id: str):
        account_id = require_session(request)
        values = await form_values(request)
        require_csrf(request, values.get("csrf"))
        await run_in_threadpool(service.revoke_key, account_id, key_id)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/v1/campaigns")
    async def campaigns():
        return {"source_url": SOURCE_URL, "source_date": SOURCE_DATE,
                "worker_image": settings.public_worker_image,
                "cpu_worker_image": getattr(settings, "public_cpu_worker_image", ""),
                "multicurve_enabled": multicurve_enabled,
                "campaigns": [challenge.public(multicurve_enabled) for challenge in CHALLENGES]}

    @app.get("/v1/config")
    async def config(campaign: str = CAMPAIGN):
        challenge = BY_ID.get(campaign)
        if challenge is None:
            raise Rejected(404, "unknown campaign")
        if challenge.contribution != "ecc2k130_gpu_v1":
            raise Rejected(409, "this campaign has no compatible worker and verifier yet")
        return {"campaign": CAMPAIGN, "walk_id": WALK_ID,
                "dp_weight": DP_WEIGHT, "supported_dp_weights": list(DP_WEIGHTS),
                "recommended_dp_weight": RECOMMENDED_DP_WEIGHT, "record_bytes": RECORD_BYTES,
                "max_batch_bytes": MAX_BATCH_BYTES}

    @app.post("/v1/heartbeat")
    async def heartbeat(request: Request):
        # Shared by both worker protocols; a ping never counts as point activity.
        await limited_body(request, 0)
        await run_in_threadpool(service.authenticate, bearer_value(request), heartbeat=True)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @app.post("/v1/register")
    async def register(request: Request):
        if await limited_body(request, 0):
            raise Rejected(400, "registration body is not used")
        if request.headers.get("x-campaign-id", CAMPAIGN) != CAMPAIGN:
            raise Rejected(409, "this campaign has no compatible worker and verifier yet")
        key = await run_in_threadpool(service.authenticate, bearer_value(request))
        try:
            dp_weight = int(request.headers.get("x-dp-weight", str(DP_WEIGHT)))
        except ValueError:
            raise Rejected(400, "invalid reporting threshold") from None
        if request.headers.get("x-walk-id", WALK_ID) != WALK_ID:
            raise Rejected(409, "this endpoint accepts only the legacy sigma walk")
        result = await run_in_threadpool(service.register, key, request.headers.get("x-registration-id", ""), dp_weight)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    @app.get("/v1/runs/{run_id}")
    async def run_status(request: Request, run_id: int):
        key = await run_in_threadpool(service.authenticate, bearer_value(request))
        result = await run_in_threadpool(service.run_status, key, run_id)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/v1/runs/{run_id}/dps")
    async def upload_dps(request: Request, run_id: int, offset: int):
        if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
            raise Rejected(415, "binary content type required")
        length = declared_binary_length(request, MAX_BATCH_BYTES)
        key = await run_in_threadpool(service.authenticate, bearer_value(request),
                                      max(1, (length + 65535) // 65536))
        data = await limited_body(request, MAX_BATCH_BYTES)
        if len(data) != length:
            raise Rejected(400, "body length differs from Content-Length")
        result = await run_in_threadpool(service.upload_batch, key, run_id, offset, data)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    @app.post("/v1/runs/{run_id}/checkpoint")
    async def upload_checkpoint(request: Request, run_id: int, offset: int):
        if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
            raise Rejected(415, "binary content type required")
        length = declared_binary_length(request, MAX_CHECKPOINT_BYTES)
        key = await run_in_threadpool(service.authenticate, bearer_value(request),
                                      max(1, (length + 2097151) // 2097152))
        async with checkpoint_slots:
            data = await limited_body(request, MAX_CHECKPOINT_BYTES)
            if len(data) != length:
                raise Rejected(400, "body length differs from Content-Length")
            result = await run_in_threadpool(service.upload_checkpoint, key, run_id, offset, data)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    @app.get("/v1/runs/{run_id}/checkpoint")
    async def download_checkpoint(request: Request, run_id: int):
        key = await run_in_threadpool(service.authenticate, bearer_value(request))
        body, offset = await run_in_threadpool(service.download_checkpoint, key, run_id)
        return Response(body, media_type="application/octet-stream",
                        headers={"X-DP-Cursor": str(offset), "Cache-Control": "no-store"})

    return app
