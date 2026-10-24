from dataclasses import replace
from uuid import uuid4

import pytest

from ecc_challenges.service import Rejected, Service
from test_pipeline import record


def test_more_than_three_active_keys_share_daily_upload_ceiling(database, aws_resources, settings):
    storage, *_ = aws_resources
    service = Service(database, storage, replace(settings, max_key_bytes_per_day=64))
    service.upsert_account(1234, "contributor")
    secrets = []
    for index in range(4):
        if index:
            with database.connection() as conn:
                conn.execute("""
                    UPDATE ecc_public.api_keys SET created_at=now()-interval '2 minutes'
                    WHERE account_id=%s
                """, (1234,))
        secrets.append(service.create_key(1234, f"worker {index}"))
    assert len([key for key in service.account_keys(1234) if key["revoked_at"] is None]) == 4

    for secret in secrets[:3]:
        key = service.authenticate(secret)
        run_id = service.register(key, str(uuid4()))["run_id"]
        service.upload_batch(key, run_id, 0, record(run_id, 0))
        service.upload_batch(key, run_id, 32, record(run_id, 1))

    fourth = service.authenticate(secrets[3])
    fourth_run = service.register(fourth, str(uuid4()))["run_id"]
    with pytest.raises(Rejected, match="account daily upload quota reached") as error:
        service.upload_batch(fourth, fourth_run, 0, record(fourth_run, 0))
    assert error.value.status == 429
    with database.connection() as conn:
        assert conn.execute("SELECT bytes FROM ecc_public.account_usage_day WHERE account_id=1234").fetchone()["bytes"] == 192
        assert conn.execute("SELECT count(*) AS keys FROM ecc_public.key_usage_day").fetchone()["keys"] == 3


def test_key_creation_spacing_still_applies(database, aws_resources, settings):
    storage, *_ = aws_resources
    service = Service(database, storage, settings)
    service.upsert_account(1234, "contributor")
    service.create_key(1234, "first")
    with pytest.raises(Rejected, match="wait one minute") as error:
        service.create_key(1234, "second")
    assert error.value.status == 429
