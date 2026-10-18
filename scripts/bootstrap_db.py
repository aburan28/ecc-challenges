#!/usr/bin/env python3
"""Install the public schema and limited login roles in the chosen account.

Passwords are fetched from Secrets Manager and never passed on argv.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3
import psycopg
from psycopg import sql


def secret(client, name: str) -> str:
    return client.get_secret_value(SecretId=name)["SecretString"]


def set_login(conn, name: str, password: str, limit: int) -> None:
    with conn.cursor() as cursor:
        exists = cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
        if not exists:
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT {}")
                           .format(sql.Identifier(name), sql.Literal(limit)))
        cursor.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(name), sql.Literal(password)))
        cursor.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(sql.Identifier(name), sql.Literal(limit)))
        cursor.execute(sql.SQL("ALTER ROLE {} SET search_path TO ecc_public,public").format(sql.Identifier(name)))


def verify_logins(args, web_password: str, worker_password: str) -> None:
    for role, password, campaign_read, candidate_write, totals_read in (
        ("ecc_site_web", web_password, False, False, True),
        ("ecc_site_worker", worker_password, True, True, False),
    ):
        with psycopg.connect(host=args.host, port=5432, dbname=args.database,
                             user=role, password=password, sslmode="verify-full",
                             sslrootcert=str(args.ca), connect_timeout=15) as conn:
            actual = conn.execute("""
                SELECT has_column_privilege(current_user,'public.distinguished_points','campaign_id','SELECT')
                       AND has_column_privilege(current_user,'public.distinguished_points','point_key','SELECT')
                       AND has_column_privilege(current_user,'public.distinguished_points','walk_seed','SELECT')
                       AND has_column_privilege(current_user,'public.distinguished_points','worker_id','SELECT'),
                       has_table_privilege(current_user,'ecc_public.candidate_points','INSERT'),
                       has_table_privilege(current_user,'ecc_public.historical_import_totals','SELECT')
            """).fetchone()
            if actual != (campaign_read, candidate_write, totals_read):
                raise RuntimeError(f"unexpected privileges for {role}: {actual}")
            conn.execute("SELECT count(*) FROM ecc_public.runs").fetchone()
            print(f"{role}: TLS login, schema, and permissions verified", flush=True)


def bootstrap(args) -> None:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual != args.account_id:
        raise RuntimeError(f"AWS account mismatch: expected {args.account_id}, got {actual}")
    sm = session.client("secretsmanager")
    owner = json.loads(secret(sm, args.owner_secret))
    web_password = secret(sm, args.web_password_secret)
    worker_password = secret(sm, args.worker_password_secret)
    if any(not value or len(value) < 32 for value in (web_password, worker_password)):
        raise ValueError("generated database passwords are missing or too short")
    with psycopg.connect(host=args.host, port=5432, dbname=args.database,
                         user=owner["username"], password=owner["password"],
                         sslmode="verify-full", sslrootcert=str(args.ca),
                         connect_timeout=15, autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(710130001)")
            try:
                for migration in sorted(args.schema.glob("*.sql")):
                    cursor.execute(migration.read_text())
                with conn.transaction():
                    set_login(conn, "ecc_site_web", web_password, 128)
                    # Up to 32 audit + 8 index + 8 resolver + 1 reconciler,
                    # with two-connection pools and 200% rolling deployments.
                    set_login(conn, "ecc_site_worker", worker_password, 256)
                    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO ecc_site_web,ecc_site_worker")
                                   .format(sql.Identifier(args.database)))
                    cursor.execute("GRANT USAGE ON SCHEMA ecc_public TO ecc_site_web,ecc_site_worker")
                    cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA ecc_public FROM ecc_site_web,ecc_site_worker")
                    cursor.execute("""
                        GRANT SELECT,INSERT,UPDATE ON
                          ecc_public.accounts,ecc_public.api_keys,ecc_public.runs,
                          ecc_public.key_usage_day,ecc_public.batches
                        TO ecc_site_web
                    """)
                    cursor.execute("GRANT SELECT,UPDATE ON ecc_public.pressure_shards TO ecc_site_web")
                    cursor.execute("GRANT SELECT ON ecc_public.historical_import_totals TO ecc_site_web")
                    cursor.execute("""
                        GRANT SELECT,INSERT,UPDATE ON ecc_public.dp_namespaces,
                          ecc_public.dp_runs,ecc_public.dp_batches TO ecc_site_web
                    """)
                    cursor.execute("GRANT SELECT,UPDATE ON ecc_public.dp_pressure TO ecc_site_web")
                    cursor.execute("GRANT SELECT,INSERT,UPDATE ON ecc_public.dp_namespace_stats TO ecc_site_web,ecc_site_worker")
                    cursor.execute("GRANT SELECT,INSERT ON ecc_public.dp_index_bindings TO ecc_site_worker")
                    cursor.execute("GRANT SELECT,INSERT,UPDATE ON ecc_public.dp_point_index TO ecc_site_worker")
                    cursor.execute("""
                        GRANT SELECT ON ecc_public.accounts,ecc_public.dp_namespaces TO ecc_site_worker;
                        GRANT SELECT,UPDATE ON ecc_public.dp_runs,ecc_public.dp_batches,
                          ecc_public.dp_pressure TO ecc_site_worker;
                        GRANT SELECT,INSERT,UPDATE ON ecc_public.dp_collision_leads TO ecc_site_worker
                    """)
                    cursor.execute("""
                        GRANT SELECT,UPDATE ON
                          ecc_public.api_keys,ecc_public.runs,ecc_public.batches,
                          ecc_public.pressure_shards,ecc_public.candidate_points,
                          ecc_public.collision_leads
                        TO ecc_site_worker
                    """)
                    cursor.execute("GRANT INSERT ON ecc_public.candidate_points,ecc_public.collision_leads TO ecc_site_worker")
                    cursor.execute("GRANT SELECT,INSERT ON ecc_public.audit_events,ecc_public.indexed_batches TO ecc_site_worker")
                    cursor.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA ecc_public TO ecc_site_web,ecc_site_worker")
                    cursor.execute("GRANT USAGE ON SCHEMA public TO ecc_site_worker")
                    cursor.execute("""
                        GRANT SELECT (campaign_id,point_key,walk_seed,worker_id)
                        ON public.distinguished_points TO ecc_site_worker
                    """)
                    cursor.execute("ALTER ROLE ecc_site_web SET statement_timeout TO '30s'")
                    cursor.execute("ALTER ROLE ecc_site_web SET idle_in_transaction_session_timeout TO '60s'")
                    cursor.execute("ALTER ROLE ecc_site_worker SET statement_timeout TO '10min'")
                    cursor.execute("ALTER ROLE ecc_site_worker SET idle_in_transaction_session_timeout TO '15min'")
                print("ecc_public schema and limited roles ready", flush=True)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(710130001)")
    verify_logins(args, web_password, worker_password)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", help="named AWS profile; omit when using an ECS task role")
    parser.add_argument("--account-id", required=True, help="expected destination AWS account ID")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-west-2"))
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", default="rho_dp")
    parser.add_argument("--owner-secret", required=True)
    parser.add_argument("--web-password-secret", default="ecc-challenges/web-db-password")
    parser.add_argument("--worker-password-secret", default="ecc-challenges/worker-db-password")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--schema", type=Path, default=root / "sql")
    parser.add_argument("--ca", type=Path, default=root / "infra/rds-ca.pem")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    bootstrap(args)


if __name__ == "__main__":
    main()
