#!/usr/bin/env python3
"""Prepare and use the local development stack in deploy/local/.

  init  wait for PostgreSQL and the AWS emulator, apply migrations once, and
        create the bucket, queues and point-index table
  key   create a contributor account and print a new API key

Runs inside the stack's web image (see deploy/local/compose.yaml). It only
ever talks to the local emulator: it refuses to run unless LOCAL_STACK=1.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import boto3
from botocore.config import Config
import psycopg

ROOT = Path(__file__).resolve().parents[1]
SQL = Path(os.environ.get("ECC_SQL_DIR", ROOT / "sql"))
QUEUES = ("audit", "index", "collision")


def wait(what: str, check, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return check()
        except Exception as exc:  # noqa: BLE001 - report the last failure only
            if time.monotonic() > deadline:
                raise SystemExit(f"{what} did not become ready: {exc}") from None
            time.sleep(1)


def init() -> None:
    database_url = os.environ["DATABASE_URL"]
    wait("PostgreSQL", lambda: psycopg.connect(database_url, connect_timeout=3).close())
    # Migrations are idempotent, so newer ones apply to an existing database.
    with psycopg.connect(database_url, autocommit=True) as conn:
        for migration in sorted(SQL.glob("*.sql")):
            conn.execute(migration.read_text())
            print(f"applied {migration.name}")

    region = os.environ.get("AWS_REGION", "us-west-2")
    s3 = boto3.client("s3", region_name=region, config=Config(signature_version="s3v4"))
    bucket = os.environ["ECC_BUCKET"]
    wait("S3 emulator", s3.list_buckets)
    if bucket not in {b["Name"] for b in s3.list_buckets()["Buckets"]}:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_object(Bucket=bucket, Key="seed-registry/curve-131/config.json",
                  Body=b'{"version": 1, "state": "ready"}')

    sqs = boto3.client("sqs", region_name=region)
    for name in QUEUES:
        sqs.create_queue(QueueName=name)

    dynamodb = boto3.client("dynamodb", region_name=region)
    table = os.environ["POINT_INDEX_TABLE"]
    if table not in dynamodb.list_tables()["TableNames"]:
        dynamodb.create_table(TableName=table, BillingMode="PAY_PER_REQUEST",
                              KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                              AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}])
    print("local stack ready")


def key(login: str) -> None:
    from ecc_challenges.db import Database
    from ecc_challenges.service import Service
    from ecc_challenges.settings import Settings
    from ecc_challenges.storage import Storage

    settings = Settings.from_env(web=False)
    db = Database(settings.database_url, max_size=1)
    db.open()
    try:
        service = Service(db, Storage(None, None, settings.bucket, settings.stream_id), settings)
        account_id = 1_000_000 + sum(login.encode())
        service.upsert_account(account_id, login)
        print(service.create_key(account_id, "local stack"))
    finally:
        db.close()


def main() -> None:
    if os.environ.get("LOCAL_STACK") != "1":
        raise SystemExit("refusing to run outside the local stack (LOCAL_STACK=1 is not set)")
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "init":
        init()
    elif command == "key":
        key(sys.argv[2] if len(sys.argv) > 2 else "local-contributor")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
