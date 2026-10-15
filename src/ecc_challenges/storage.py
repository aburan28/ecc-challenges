"""Write-once campaign archive, shared seed registry, and durable queue adapters."""

from __future__ import annotations

import base64
import hashlib
import json
from botocore.exceptions import ClientError


class Storage:
    def __init__(self, s3, sqs, bucket: str, stream_id: str):
        self.s3 = s3
        self.sqs = sqs
        self.bucket = bucket
        self.stream_id = stream_id

    def assert_registry_ready(self) -> None:
        obj = self.s3.get_object(Bucket=self.bucket, Key="seed-registry/curve-131/config.json")
        row = json.loads(obj["Body"].read())
        if row.get("version") != 1 or row.get("state") != "ready":
            raise RuntimeError("shared seed registry is not ready")

    def reserve_run(self, run_id: int) -> bool:
        self.assert_registry_ready()
        owner = f"public-gateway:{self.stream_id}:run{run_id}"
        row = {"version": 1, "run_id": run_id, "stream": owner,
               "started": True, "active_owner": owner, "checkpoint_floor": 0}
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=f"seed-registry/curve-131/run-{run_id:05d}.json",
                Body=json.dumps(row, sort_keys=True).encode(),
                ContentType="application/json", IfNoneMatch="*",
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                return False
            raise

    def batch_key(self, run_id: int, offset: int, digest: str) -> str:
        return (f"public-gateway/{self.stream_id}/runs/{run_id:05d}/batches/"
                f"{offset:016d}-{digest}.bin")

    def checkpoint_key(self, run_id: int, digest: str) -> str:
        return f"public-gateway/{self.stream_id}/runs/{run_id:05d}/checkpoints/{digest}.ck"

    def collision_key(self, lead_id: int, digest: str) -> str:
        return f"public-gateway/{self.stream_id}/collision-leads/{lead_id:016d}/{digest}.json"

    def put_immutable(self, key: str, body: bytes) -> str:
        sha = hashlib.sha256(body).digest()
        try:
            self.s3.put_object(
                Bucket=self.bucket, Key=key, Body=body,
                ChecksumSHA256=base64.b64encode(sha).decode(),
                ContentType="application/octet-stream", IfNoneMatch="*",
                ServerSideEncryption="AES256",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
            existing = self.get(key)
            if existing != body:
                raise RuntimeError("immutable archive key holds different bytes") from exc
        return sha.hex()

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def send(self, queue_url: str, batch_id: int) -> None:
        self.sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"batch_id": batch_id}))

    def send_lead(self, queue_url: str, lead_id: int) -> None:
        self.sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"lead_id": lead_id}))
