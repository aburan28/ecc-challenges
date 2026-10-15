"""Account keys, constant-time verification and CSRF tokens."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

KEY_PATTERN = re.compile(r"^ecc1_([0-9a-f]{24})_([A-Za-z0-9_-]{43})$")


def generate_key(pepper: str) -> tuple[str, str, bytes]:
    key_id = secrets.token_hex(12)
    secret = secrets.token_urlsafe(32)
    if len(secret) != 43:
        raise RuntimeError("unexpected key length")
    value = f"ecc1_{key_id}_{secret}"
    return key_id, value, digest_key(pepper, key_id, secret)


def digest_key(pepper: str, key_id: str, secret: str) -> bytes:
    return hmac.digest(pepper.encode(), f"ecc1:{key_id}:{secret}".encode(), "sha256")


def parse_and_digest(pepper: str, value: str) -> tuple[str, bytes] | None:
    match = KEY_PATTERN.fullmatch(value)
    if not match:
        return None
    key_id, secret = match.groups()
    return key_id, digest_key(pepper, key_id, secret)


def same_digest(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def new_csrf() -> str:
    return secrets.token_urlsafe(24)
