"""Small authenticated control messages; witness bytes travel directly to S3."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..service import Rejected
from .archive import Archive
from .service import Service

BODY_LIMIT = 4096


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer ") or len(value) > 160:
        raise Rejected(401, "API key required")
    return value[7:]


async def _body(request: Request, limit: int = BODY_LIMIT) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise Rejected(400, "invalid Content-Length") from None
        if length < 0:
            raise Rejected(400, "invalid Content-Length")
        if length > limit:
            raise Rejected(413, "request body too large")
    result = bytearray()
    async for chunk in request.stream():
        if len(result) + len(chunk) > limit:
            raise Rejected(413, "request body too large")
        result.extend(chunk)
    if declared is not None and len(result) != length:
        raise Rejected(400, "body length differs from Content-Length")
    return bytes(result)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _constant(_value):
    raise ValueError("nonfinite JSON number")


async def _json(request: Request, fields: set[str]) -> dict:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise Rejected(415, "JSON content type required")
    raw = await _body(request)
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise Rejected(400, "invalid JSON body") from None
    if type(value) is not dict or set(value) != fields:
        raise Rejected(400, "body contains missing or unexpected fields")
    return value


def _response(value, status=200):
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


def create_router(legacy, database, storage, settings) -> APIRouter:
    service = Service(legacy, database, Archive(storage), settings)
    router = APIRouter(prefix="/v2")

    async def authenticate(request: Request, cost=1):
        key = await run_in_threadpool(legacy.authenticate, _bearer(request), cost)
        if not settings.multicurve_enabled and key["id"] not in settings.multicurve_canary_keys:
            raise Rejected(503, "multi-curve release is not enabled for this key", 60)
        return key

    @router.get("/config")
    async def config(request: Request, campaign: str):
        await authenticate(request)
        result = await run_in_threadpool(service.config, campaign)
        return _response(result)

    @router.post("/register")
    async def register(request: Request):
        key = await authenticate(request)
        value = await _json(request, {"campaign", "registration_id"})
        result = await run_in_threadpool(
            service.register, key, value["campaign"], value["registration_id"]
        )
        return _response(result, 201)

    @router.post("/runs/{run_id}/batches")
    async def intent(request: Request, run_id: str):
        credential = _bearer(request)
        key = await authenticate(request)
        value = await _json(request, {"offset", "byte_length", "records", "sha256"})
        from .service import integer
        length = integer(value["byte_length"], "byte_length", 1, 1048576)
        additional_cost = (length + 65535) // 65536 - 1
        if additional_cost:
            key = await run_in_threadpool(legacy.authenticate, credential, additional_cost)
        result = await run_in_threadpool(
            service.intent, key, run_id, value["offset"], value["byte_length"],
            value["records"], value["sha256"],
        )
        return _response(result, 201)

    @router.post("/runs/{run_id}/batches/{batch_id}/commit")
    async def commit(request: Request, run_id: str, batch_id: str):
        key = await authenticate(request)
        await _body(request, 0)
        result = await run_in_threadpool(service.commit, key, run_id, batch_id)
        return _response(result)

    @router.get("/runs/{run_id}")
    async def status(request: Request, run_id: str):
        key = await authenticate(request)
        result = await run_in_threadpool(service.run_status, key, run_id)
        return _response(result)

    return router
