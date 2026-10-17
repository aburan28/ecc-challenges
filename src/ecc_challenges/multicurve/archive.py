"""Immutable, version-pinned S3 storage and bounded direct upload capabilities."""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from .codec import canonical

HEX = re.compile(r"^[0-9a-f]{64}$")


class Archive:
    def __init__(self, storage):
        self.storage = storage
        self.s3 = storage.s3
        self.bucket = storage.bucket
        self.prefix = f"public-gateway/{storage.stream_id}/v2"

    def namespace_key(self, namespace_id: str) -> str:
        if not HEX.fullmatch(namespace_id):
            raise ValueError("invalid namespace digest")
        return f"{self.prefix}/namespaces/{namespace_id}/manifest.json"

    def ensure_namespace(self, ns) -> str:
        key = self.namespace_key(ns.id)
        self.storage.put_immutable(key, canonical(ns.public()))
        return key

    def raw_key(self, namespace_id: str, run_id: str, offset: int, digest: str) -> str:
        self.namespace_key(namespace_id)
        run = UUID(str(run_id))
        if not HEX.fullmatch(digest) or offset < 0:
            raise ValueError("invalid batch identity")
        return (f"{self.prefix}/raw/{namespace_id}/shard={digest[:2]}/runs/{run}/"
                f"{offset:020d}-{digest}.bin")

    def presign(self, key: str, digest: str, length: int, expires: int = 900) -> dict:
        if not key.startswith(self.prefix + "/raw/") or not HEX.fullmatch(digest):
            raise ValueError("invalid upload object")
        checksum = base64.b64encode(bytes.fromhex(digest)).decode()
        params = {"Bucket": self.bucket, "Key": key, "ContentLength": length,
                  "ContentType": "application/octet-stream", "ChecksumSHA256": checksum,
                  "IfNoneMatch": "*", "ServerSideEncryption": "AES256"}
        url = self.s3.generate_presigned_url("put_object", Params=params, ExpiresIn=expires,
                                             HttpMethod="PUT")
        query = parse_qs(urlsplit(url).query)
        signed = set(query.get("X-Amz-SignedHeaders", [""])[0].split(";"))
        required = {"content-length", "content-type", "if-none-match", "x-amz-checksum-sha256",
                    "x-amz-server-side-encryption"}
        if not required.issubset(signed) or "X-Amz-Signature" not in query:
            raise RuntimeError("S3 presigner did not bind every required upload control")
        return {"url": url, "method": "PUT", "headers": {
            "Content-Length": str(length), "Content-Type": "application/octet-stream",
            "If-None-Match": "*", "x-amz-checksum-sha256": checksum,
            "x-amz-server-side-encryption": "AES256"}, "expires_in": expires}

    def inspect(self, key: str, digest: str, length: int) -> str:
        obj = self.s3.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        expected = base64.b64encode(bytes.fromhex(digest)).decode()
        if obj.get("ContentLength") != length or obj.get("ChecksumSHA256") != expected:
            raise ValueError("uploaded object size or checksum does not match its intent")
        version = obj.get("VersionId")
        if not version or version == "null":
            raise RuntimeError("the archive bucket must have versioning enabled")
        return version

    def get(self, key: str, version: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key, VersionId=version)["Body"].read()

    def put_receipt(self, namespace_id: str, kind: str, identity: str, value: dict) -> str:
        self.namespace_key(namespace_id)
        if kind not in {"batches", "admissions", "verified", "collisions", "checkpoints", "compaction", "pointindex"}:
            raise ValueError("invalid receipt kind")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
            raise ValueError("invalid receipt identity")
        body = canonical(value)
        digest = hashlib.sha256(body).hexdigest()
        key = f"{self.prefix}/{kind}/{namespace_id}/{identity}/{digest}.json"
        self.storage.put_immutable(key, body)
        return key
