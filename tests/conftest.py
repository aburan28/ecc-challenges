from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import socket
import subprocess

import boto3
from moto import mock_aws
import psycopg
import pytest

from ecc_challenges.db import Database
from ecc_challenges.settings import Settings
from ecc_challenges.storage import Storage

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_dsn(tmp_path_factory):
    supplied = os.environ.get("TEST_POSTGRES_DSN")
    if supplied:
        yield supplied
        return
    if not shutil.which("initdb") or not shutil.which("pg_ctl"):
        pytest.skip("local PostgreSQL binaries are unavailable; set TEST_POSTGRES_DSN")
    root = tmp_path_factory.mktemp("postgres")
    data = root / "data"
    subprocess.run(["initdb", "-D", str(data), "-A", "trust", "-U", "postgres", "--no-instructions"],
                   check=True, capture_output=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = root / "postgres.log"
    subprocess.run(["pg_ctl", "-D", str(data), "-l", str(log), "-o",
                    f"-h 127.0.0.1 -p {port}", "start"], check=True, capture_output=True)
    try:
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "stop", "-m", "immediate"],
                       check=True, capture_output=True)


@pytest.fixture
def database(postgres_dsn):
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS ecc_public CASCADE")
        conn.execute("DROP TABLE IF EXISTS public.distinguished_points")
        for migration in sorted((ROOT / "sql").glob("*.sql")):
            conn.execute(migration.read_text())
    db = Database(postgres_dsn, max_size=3)
    db.open()
    yield db
    db.close()


@pytest.fixture
def aws_resources():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-west-2")
        s3.create_bucket(Bucket="ecc-test-corpus", CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        s3.put_object(Bucket="ecc-test-corpus", Key="seed-registry/curve-131/config.json",
                      Body=json.dumps({"version": 1, "state": "ready"}).encode())
        sqs = boto3.client("sqs", region_name="us-west-2")
        audit = sqs.create_queue(QueueName="audit-test")["QueueUrl"]
        index = sqs.create_queue(QueueName="index-test")["QueueUrl"]
        collision = sqs.create_queue(QueueName="collision-test")["QueueUrl"]
        storage = Storage(s3, sqs, "ecc-test-corpus", "test-public-v1")
        yield storage, sqs, audit, index, collision


@pytest.fixture
def settings(postgres_dsn, aws_resources):
    _, _, audit, index, collision = aws_resources
    return Settings(postgres_dsn, "ecc-test-corpus", audit, index, "us-west-2",
                    "p" * 48, "s" * 48, "gh-id", "gh-secret", "https://ecc-challenges.com",
                    50000, 65534, "test-public-v1", "/bin/false", 1000, 5 * 1024**3,
                    collision)
