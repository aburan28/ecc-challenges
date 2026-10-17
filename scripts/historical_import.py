#!/usr/bin/env python3
"""Import the original ECC2K-130 campaign's distinguished points, one way.

    # 1. In the original account, read-only. Writes nothing with --dry-run.
    historical_import.py export --source-profile ecc2k-ro --source-account 590183823895 \\
        --source-bucket ecc2k130-590183823895 --dry-run
    historical_import.py export --source-profile ecc2k-ro --source-account 590183823895 \\
        --source-bucket ecc2k130-590183823895 \\
        --parts s3://<public archive>/historical-import/2026-10 \\
        --parts-profile ecc-public --parts-account <public account id>

    # 2. In the public account, with the database owner's DSN in HISTORICAL_DSN.
    historical_import.py load --parts s3://... --parts-profile ecc-public --parts-account <id>

    # 3. After the last load, with the public archive bucket.
    historical_import.py backmatch --bucket <public archive> --stream-id <stream> \\
        --profile ecc-public --account <id>

    # Optional: compare per-worker counts with the original RDS view
    # (read-only DSN in SOURCE_DSN; that view lags S3, so short counts there are expected).
    historical_import.py reconcile

Every subcommand prints one JSON summary on stdout. Credentials come from named
profiles and DSNs from the environment, never from argv.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import boto3
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ecc_challenges import historical  # noqa: E402
from ecc_challenges.storage import Storage  # noqa: E402


def session(profile: str | None, account: str | None, region: str):
    if not profile:
        return boto3.Session(region_name=region)
    s = boto3.Session(profile_name=profile, region_name=region)
    actual = s.client("sts").get_caller_identity()["Account"]
    if account and actual != account:
        raise SystemExit(f"profile {profile} is account {actual}, expected {account}")
    return s


def connect(env: str, read_only: bool = False):
    dsn = os.environ.get(env)
    if not dsn:
        raise SystemExit(f"set {env} to the database DSN")
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
    if read_only:
        conn.execute("SET default_transaction_read_only = on")
    return conn


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="us-west-2")
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("export")
    ex.add_argument("--source-profile", required=True)
    ex.add_argument("--source-account", required=True)
    ex.add_argument("--source-bucket", required=True)
    ex.add_argument("--parts", help="local directory or s3://bucket/prefix")
    ex.add_argument("--parts-profile")
    ex.add_argument("--parts-account")
    ex.add_argument("--limit", type=int)
    ex.add_argument("--dry-run", action="store_true")

    ld = sub.add_parser("load")
    ld.add_argument("--parts", required=True)
    ld.add_argument("--parts-profile")
    ld.add_argument("--parts-account")
    ld.add_argument("--dsn-env", default="HISTORICAL_DSN")

    bm = sub.add_parser("backmatch")
    bm.add_argument("--bucket", required=True)
    bm.add_argument("--stream-id", required=True)
    bm.add_argument("--profile")
    bm.add_argument("--account")
    bm.add_argument("--dsn-env", default="HISTORICAL_DSN")

    rc = sub.add_parser("reconcile")
    rc.add_argument("--source-dsn-env", default="SOURCE_DSN")
    rc.add_argument("--dsn-env", default="HISTORICAL_DSN")

    args = parser.parse_args()

    if args.command == "export":
        if not args.dry_run and not args.parts:
            parser.error("export needs --parts unless --dry-run")
        source = session(args.source_profile, args.source_account, args.region).client("s3")
        parts_s3 = (session(args.parts_profile, args.parts_account, args.region).client("s3")
                    if args.parts and args.parts.startswith("s3://") else None)
        sink = historical.open_sink(args.parts, parts_s3) if args.parts else None
        summary = historical.export(source, args.source_bucket, sink, write=not args.dry_run,
                                    limit=args.limit, log=log)
    elif args.command == "load":
        parts_s3 = (session(args.parts_profile, args.parts_account, args.region).client("s3")
                    if args.parts.startswith("s3://") else None)
        with connect(args.dsn_env) as conn:
            summary = historical.load(conn, historical.open_sink(args.parts, parts_s3), log=log)
    elif args.command == "backmatch":
        s3 = session(args.profile, args.account, args.region).client("s3")
        with connect(args.dsn_env) as conn:
            summary = historical.backmatch(conn, Storage(s3, None, args.bucket, args.stream_id), log=log)
    else:
        with connect(args.source_dsn_env, read_only=True) as source, connect(args.dsn_env) as target:
            summary = historical.reconcile(historical.worker_counts(source), historical.worker_counts(target))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
