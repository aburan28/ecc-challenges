#!/usr/bin/env python3
"""Known-answer collision through the whole local stack.

Takes a real DP that a worker uploaded, registers a second run through the
public API and uploads the same point written with that run's own trail start:
b2 is trail 0's b, and a2 = a1 + (b1 - b2) * d, where d is the curve's published
reference scalar. The point, the witness algebra and the trail check are all
genuine, so the batch must pass verification, the point index must match it
against the first upload, and the resolver must recover d.

  docker compose -f deploy/local/compose.yaml run --rm -T init \\
    python scripts/local_collision_check.py eccp-79 /app/.local-key

Only for the local stack: it refuses to run unless LOCAL_STACK=1.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

import boto3
from botocore.config import Config
import psycopg
from psycopg.rows import dict_row

from ecc_challenges.multicurve.codec import Record, canonical, namespace
from ecc_challenges.multicurve.instances import registry_metadata

ORIGIN = os.environ.get("ECC_API_URL", "https://ecc-challenges.test")
CONTEXT = ssl.create_default_context(cafile=os.environ.get("AWS_CA_BUNDLE", "/ca/root.crt"))


def call(key: str, method: str, path: str, body=None) -> dict:
    data = canonical(body) if body is not None else (b"" if method == "POST" else None)
    request = Request(ORIGIN + path, method=method, data=data, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urlopen(request, timeout=60, context=CONTEXT) as response:
        return json.load(response)


def main() -> None:
    if os.environ.get("LOCAL_STACK") != "1":
        raise SystemExit("refusing to run outside the local stack (LOCAL_STACK=1 is not set)")
    campaign, key_file = sys.argv[1], sys.argv[2]
    key = open(key_file).read().strip()
    ns = namespace(campaign)
    d = int(registry_metadata()["reference_vectors"][ns.campaign]["scalar"], 16)
    if ns.instance.mul(d, ns.instance.G) != ns.instance.Q:
        raise SystemExit("published reference scalar does not satisfy dG = Q")

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        batch = conn.execute("""
            SELECT object_key,version_id,record_bytes FROM ecc_public.dp_batches
            WHERE namespace_id=%s AND state='indexed' ORDER BY indexed_at LIMIT 1
        """, (ns.id,)).fetchone()
    if batch is None:
        raise SystemExit(f"no indexed batch for {ns.campaign} yet; let a worker upload first")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"),
                      config=Config(signature_version="s3v4"))
    raw = s3.get_object(Bucket=os.environ["ECC_BUCKET"], Key=batch["object_key"],
                        VersionId=batch["version_id"])["Body"].read()
    first = ns.decode(raw[:ns.record_bytes])[0]

    run = call(key, "POST", "/v2/register", {"campaign": ns.campaign, "registration_id": str(uuid4())})
    if run["namespace_id"] != ns.id:
        raise SystemExit("gateway namespace differs from this checkout")
    b2 = ns.start(run["run_id"], 0).b
    second = Record(0, 1, first.point, (first.a + (first.b - b2) * d) % ns.instance.n, b2)
    ns.verify(second)
    ns.check_trail(second, run["run_id"])
    data = ns.encode(second)
    digest = hashlib.sha256(data).hexdigest()
    intent = call(key, "POST", f"/v2/runs/{run['run_id']}/batches", {
        "offset": 0, "byte_length": len(data), "records": 1, "sha256": digest})
    upload = intent["upload"]
    with urlopen(Request(upload["url"], method="PUT", data=data, headers=upload["headers"]),
                 timeout=60, context=CONTEXT) as response:
        if response.status != 200:
            raise SystemExit("signed upload was not acknowledged")
    call(key, "POST", f"/v2/runs/{run['run_id']}/batches/{intent['batch_id']}/commit")
    print(json.dumps({"kind": "injected", "campaign": ns.campaign, "run_id": run["run_id"],
                      "batch_id": intent["batch_id"], "point": ns.point_key(first).hex()}), flush=True)

    deadline = time.monotonic() + float(os.environ.get("CHECK_TIMEOUT_SECONDS", "300"))
    while time.monotonic() < deadline:
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
            lead = conn.execute("""
                SELECT id,state,result_key FROM ecc_public.dp_collision_leads
                WHERE namespace_id=%s AND point_key=%s AND (first_batch=%s OR second_batch=%s)
            """, (ns.id, ns.point_key(first), intent["batch_id"], intent["batch_id"])).fetchone()
        if lead and lead["state"] != "pending":
            result = json.loads(s3.get_object(Bucket=os.environ["ECC_BUCKET"],
                                              Key=lead["result_key"])["Body"].read())
            ok = lead["state"] == "verified" and int(result["scalar"], 16) == d
            print(json.dumps({"kind": "collision", "lead_id": lead["id"], "state": lead["state"],
                              "scalar": result["scalar"], "matches_reference": ok}), flush=True)
            raise SystemExit(0 if ok else 1)
        time.sleep(5)
    raise SystemExit("no resolved collision lead before the timeout")


if __name__ == "__main__":
    main()
