#!/usr/bin/env python3
"""Read-only post-bootstrap check of service permissions and schema shape."""

from __future__ import annotations

from pathlib import Path

import boto3
import psycopg


HOST = "rho-dp.cxqyicswioh8.us-west-2.rds.amazonaws.com"
CA = Path(__file__).resolve().parents[1] / "infra/rds-ca.pem"


def main() -> None:
    sm = boto3.client("secretsmanager", region_name="us-west-2")
    expected = {
        "ecc_site_web": ("ecc-challenges/web-db-password", False, False),
        "ecc_site_worker": ("ecc-challenges/worker-db-password", True, True),
    }
    for role, (secret_id, legacy_read, candidate_write) in expected.items():
        password = sm.get_secret_value(SecretId=secret_id)["SecretString"]
        with psycopg.connect(host=HOST, dbname="rho_dp", user=role, password=password,
                             sslmode="verify-full", sslrootcert=str(CA), connect_timeout=15) as conn:
            row = conn.execute("""
                SELECT has_column_privilege(current_user,'public.distinguished_points','campaign_id','SELECT')
                       AND has_column_privilege(current_user,'public.distinguished_points','point_key','SELECT')
                       AND has_column_privilege(current_user,'public.distinguished_points','walk_seed','SELECT')
                       AND has_column_privilege(current_user,'public.distinguished_points','worker_id','SELECT'),
                       has_table_privilege(current_user,'ecc_public.candidate_points','INSERT'),
                       (SELECT count(*) FROM ecc_public.runs)
            """).fetchone()
            if row[0] != legacy_read or row[1] != candidate_write:
                raise RuntimeError(f"unexpected permissions for {role}: legacy_read={row[0]}, candidate_write={row[1]}")
            print(f"{role}: TLS connection, permissions, and schema OK; public runs={row[2]}")


if __name__ == "__main__":
    main()
