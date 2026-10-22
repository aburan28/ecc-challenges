from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import uuid

import pytest
from fastapi.testclient import TestClient

from ecc_challenges.app import create_app
from ecc_challenges.protocol import check_records
from ecc_challenges.queues import QueueWorker
from ecc_challenges.service import Rejected, Service


def account(service):
    service.upsert_account(1234, "contributor")
    secret = service.create_key(1234, "test GPU")
    return secret, service.authenticate(secret)


def record(run_id: int, counter: int, point: int = 0) -> bytes:
    seed = (run_id << 48) | counter
    return seed.to_bytes(8, "little") + point.to_bytes(24, "little")


def checkpoint(run_id: int) -> bytes:
    threads, batch = 256, 16
    field_words = 16 * 1088
    size = 104 + 2 * field_words * 4 + threads * batch * 20
    body = bytearray(size)
    struct.pack_into("<8s6IQ", body, 0, b"ECC2K130", 0x100, 131, threads, batch, 1, run_id, 0)
    struct.pack_into("<iIQ", body, 40, 32, field_words, 2**32)
    return bytes(body)


def test_key_registration_and_revocation(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    secret, key = account(service)
    nonce = str(uuid.uuid4())
    first = service.register(key, nonce)["run_id"]
    assert service.register(key, nonce)["run_id"] == first
    assert service.register(key, str(uuid.uuid4()))["run_id"] != first
    assert storage.s3.get_object(Bucket=settings.bucket,
                                 Key=f"seed-registry/curve-131/run-{first:05d}.json")
    service.revoke_key(1234, key["id"])
    with pytest.raises(Rejected) as error:
        service.authenticate(secret)
    assert error.value.status == 401
    with pytest.raises(Rejected):
        service.run_status(key, first)


def test_lifetime_run_limit_preserves_finite_seed_namespace(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    service = Service(database, storage, replace(settings, max_account_runs_lifetime=2))
    _, key = account(service)
    first = service.register(key, str(uuid.uuid4()))["run_id"]
    second = service.register(key, str(uuid.uuid4()))["run_id"]
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET suspended_at=now() WHERE run_id=%s", (first,))
    with pytest.raises(Rejected) as error:
        service.register(key, str(uuid.uuid4()))
    assert error.value.status == 429
    with database.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ecc_public.runs").fetchone()["n"] == 2
    assert second == first + 1


def test_upload_idempotency_quarantine_and_failed_replay(database, aws_resources, settings, tmp_path):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    body = record(run_id, 0)
    assert check_records(body, run_id) == 1  # Syntactically plausible, not a real trail.
    assert service.upload_batch(key, run_id, 0, body)["cursor"] == 32
    assert service.upload_batch(key, run_id, 0, body)["duplicate"] is True
    with pytest.raises(Rejected) as changed:
        service.upload_batch(key, run_id, 0, record(run_id, 1))
    assert changed.value.status == 409
    with database.connection() as conn:
        row = conn.execute("SELECT * FROM ecc_public.batches").fetchone()
        assert conn.execute("SELECT count(*) AS n FROM public.distinguished_points").fetchone()["n"] == 0
    verifier = tmp_path / "reject-replay"
    verifier.write_text("#!/bin/sh\nexit 2\n")
    verifier.chmod(0o700)
    worker = QueueWorker(database, storage, replace(settings, replay_binary=str(verifier)), sqs)
    assert worker.audit_one(row["id"], timeout_seconds=2) == "failed"
    with database.connection() as conn:
        assert conn.execute("SELECT audit_state FROM ecc_public.batches").fetchone()["audit_state"] == "failed"
        assert conn.execute("SELECT count(*) AS n FROM ecc_public.candidate_points").fetchone()["n"] == 0
        assert conn.execute("SELECT suspended_at FROM ecc_public.runs").fetchone()["suspended_at"] is not None
    assert storage.get(row["object_key"]) == body


def test_bulk_index_public_and_legacy_collision_leads(database, aws_resources, settings):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    for offset, counter in ((0, 0), (32, 1)):
        service.upload_batch(key, run_id, offset, record(run_id, counter))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s", (run_id,))
        conn.execute("UPDATE ecc_public.batches SET audit_state='sampled'")
        batches = conn.execute("SELECT id FROM ecc_public.batches ORDER BY byte_offset").fetchall()
    worker = QueueWorker(database, storage, settings, sqs)
    assert worker.index_one(batches[0]["id"]) == "indexed"
    assert worker.index_one(batches[1]["id"]) == "indexed"
    assert worker.index_one(batches[1]["id"]) == "already_done"
    with database.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ecc_public.candidate_points").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM ecc_public.collision_leads WHERE prior_source='public'").fetchone()["n"] == 1
        assert conn.execute("SELECT pending_batches FROM ecc_public.pressure_shards WHERE shard=%s",
                            (run_id % 16,)).fetchone()["pending_batches"] == 0

    # An existing campaign point remains read-only; new public data produces a lead.
    with database.connection() as conn:
        conn.execute("INSERT INTO public.distinguished_points VALUES (%s,%s,%s,%s)",
                     ("ecc2k-130", bytes(24), b"\x01", "legacy-worker"))
    service.upload_batch(key, run_id, 64, record(run_id, 2))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' WHERE byte_offset=64")
        third = conn.execute("SELECT id FROM ecc_public.batches WHERE byte_offset=64").fetchone()["id"]
    assert worker.index_one(third) == "indexed"
    with database.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ecc_public.collision_leads WHERE prior_source='campaign'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM public.distinguished_points").fetchone()["n"] == 1


def test_outbox_repairs_send_failure(database, aws_resources, settings):
    storage, sqs, audit, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    original = storage.send
    storage.send = lambda *_: (_ for _ in ()).throw(ConnectionError("queue down"))
    service.upload_batch(key, run_id, 0, record(run_id, 0))
    with database.connection() as conn:
        assert conn.execute("SELECT audit_enqueued_at FROM ecc_public.batches").fetchone()["audit_enqueued_at"] is None
    storage.send = original
    worker = QueueWorker(database, storage, settings, sqs)
    assert worker.repair_outbox() == 1
    assert len(sqs.receive_message(QueueUrl=audit).get("Messages", [])) == 1


def test_full_pressure_shard_rejects_before_archival(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    service = Service(database, storage, replace(settings, max_pending_batches=16))
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    service.upload_batch(key, run_id, 0, record(run_id, 0))
    with pytest.raises(Rejected) as error:
        service.upload_batch(key, run_id, 32, record(run_id, 1))
    assert error.value.status == 503 and error.value.retry_after == 60
    with database.connection() as conn:
        assert conn.execute("SELECT cursor FROM ecc_public.runs WHERE run_id=%s", (run_id,)).fetchone()["cursor"] == 32
        assert conn.execute("SELECT pending_batches FROM ecc_public.pressure_shards WHERE shard=%s",
                            (run_id % 16,)).fetchone()["pending_batches"] == 1
    keys = storage.s3.list_objects_v2(Bucket=settings.bucket,
                Prefix=f"public-gateway/{settings.stream_id}/runs/{run_id:05d}/batches/")
    assert keys["KeyCount"] == 1


def test_later_failed_replay_quarantines_already_indexed_points(database, aws_resources, settings, tmp_path):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    secret, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    service.upload_batch(key, run_id, 0, record(run_id, 0))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s", (run_id,))
        conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' WHERE run_id=%s", (run_id,))
        first = conn.execute("SELECT id FROM ecc_public.batches WHERE run_id=%s", (run_id,)).fetchone()["id"]
    verifier = tmp_path / "reject-replay"
    verifier.write_text("#!/bin/sh\nexit 2\n")
    verifier.chmod(0o700)
    worker = QueueWorker(database, storage, replace(settings, replay_binary=str(verifier)), sqs)
    assert worker.index_one(first) == "indexed"
    service.upload_batch(key, run_id, 32, record(run_id, 1))
    with database.connection() as conn:
        second = conn.execute("SELECT id FROM ecc_public.batches WHERE byte_offset=32").fetchone()["id"]
        conn.execute("UPDATE ecc_public.batches SET audit_indices='[0]'::jsonb WHERE id=%s", (second,))
    assert worker.audit_one(second, timeout_seconds=2) == "failed"
    assert worker.index_one(second) == "discarded"
    with database.connection() as conn:
        assert conn.execute("SELECT disqualified_at FROM ecc_public.candidate_points").fetchone()["disqualified_at"]
        assert conn.execute("SELECT pending_batches FROM ecc_public.pressure_shards WHERE shard=%s",
                            (run_id % 16,)).fetchone()["pending_batches"] == 0
    with pytest.raises(Rejected):
        service.authenticate(secret)


def test_collision_lead_requires_solver_verification(database, aws_resources, settings, tmp_path):
    storage, sqs, _, _, collision_queue = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    service.upload_batch(key, run_id, 0, record(run_id, 0))
    service.upload_batch(key, run_id, 32, record(run_id, 1))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s", (run_id,))
        conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' WHERE run_id=%s", (run_id,))
        batches = conn.execute("SELECT id FROM ecc_public.batches ORDER BY byte_offset").fetchall()
    replay = tmp_path / "replay"
    replay.write_text("#!/bin/sh\ncat >/dev/null\necho '{\"match\":true}'\n")
    replay.chmod(0o700)
    solver = tmp_path / "solver"
    solver.write_text("#!/bin/sh\nprintf '  k = 7\\n  verified [k]P == Q\\n'\n")
    solver.chmod(0o700)
    worker = QueueWorker(database, storage, replace(settings, replay_binary=str(replay),
                                                    solver_binary=str(solver)), sqs)
    assert worker.index_one(batches[0]["id"]) == "indexed"
    assert worker.index_one(batches[1]["id"]) == "indexed"
    with database.connection() as conn:
        lead = conn.execute("SELECT id FROM ecc_public.collision_leads").fetchone()["id"]
    assert worker.repair_outbox() >= 1
    assert sqs.receive_message(QueueUrl=collision_queue).get("Messages")
    assert worker.resolve_one(lead, timeout_seconds=2) == "unverified"
    with database.connection() as conn:
        result = conn.execute("SELECT replay_state,result_key FROM ecc_public.collision_leads WHERE id=%s",
                              (lead,)).fetchone()
    assert result["replay_state"] == "unverified"
    receipt = storage.get(result["result_key"])
    assert b'"host_verified": true' in receipt
    assert b'"verified": false' in receipt


def test_checkpoint_uses_same_daily_byte_quota(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    service = Service(database, storage, replace(settings,
                      max_key_bytes_per_day=len(checkpoint(50000)) + 20))
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    service.upload_batch(key, run_id, 0, record(run_id, 0))
    with pytest.raises(Rejected) as error:
        service.upload_checkpoint(key, run_id, 32, checkpoint(run_id))
    assert error.value.status == 429
    with database.connection() as conn:
        assert conn.execute("SELECT checkpoint_key FROM ecc_public.runs").fetchone()["checkpoint_key"] is None


def test_http_worker_protocol(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    secret, _ = account(service)
    app = create_app(settings, db=database, storage=storage)
    with TestClient(app, base_url="https://ecc-challenges.com") as client:
        assert client.get("/v1/config").json()["dp_weight"] == 32
        nonce = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {secret}", "X-Registration-Id": nonce}
        response = client.post("/v1/register", headers=headers)
        assert response.status_code == 201
        run_id = response.json()["run_id"]
        assert client.post("/v1/register", headers=headers).json()["run_id"] == run_id
        denied = client.post(f"/v1/runs/{run_id}/dps?offset=0", content=record(run_id, 7),
                             headers={"Content-Type": "application/octet-stream"})
        assert denied.status_code == 401
        upload = client.post(f"/v1/runs/{run_id}/dps?offset=0", content=record(run_id, 7),
                             headers={"Authorization": f"Bearer {secret}",
                                      "Content-Type": "application/octet-stream"})
        assert upload.status_code == 201 and upload.json()["cursor"] == 32
        assert client.get(f"/v1/runs/{run_id}", headers=headers).json()["cursor"] == 32
        oversized = client.post(f"/v1/runs/{run_id}/dps?offset=32", content=b"x" * (1024 * 1024 + 1),
                                headers={"Authorization": f"Bearer {secret}",
                                         "Content-Type": "application/octet-stream"})
        assert oversized.status_code == 413


def test_footer_credits_isogeny_labs_with_served_logos(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    app = create_app(settings, db=database, storage=storage)
    with TestClient(app, base_url="https://ecc-challenges.com") as client:
        page = client.get("/status").text
        assert "Powered by" in page and 'href="https://isogenylabs.io/"' in page
        for scheme in ("light", "dark"):
            assert f"/assets/isogeny-labs-{scheme}.png" in page
            logo = client.get(f"/assets/isogeny-labs-{scheme}.png")
            assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
            assert logo.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert client.get("/assets/missing.png").status_code == 404
        assert client.get("/assets/..%2fapp.py").status_code == 404


def test_terminal_audit_preserves_evidence_and_releases_admission(database, aws_resources, settings, tmp_path):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, replace(settings, max_pending_batches=16))
    _, key = account(service)
    run_id = service.register(key, str(uuid.uuid4()))['run_id']
    body = record(run_id, 0)
    service.upload_batch(key, run_id, 0, body)
    verifier = tmp_path / 'unavailable-replay'
    verifier.write_text('#!/bin/sh\nexit 3\n')
    verifier.chmod(0o700)
    worker = QueueWorker(database, storage, replace(settings, replay_binary=str(verifier)), sqs)
    with database.connection() as conn:
        batch = conn.execute('SELECT * FROM ecc_public.batches').fetchone()
    assert worker.audit_one(batch['id']) == 'unverified'
    with pytest.raises(Rejected):
        service.upload_batch(key, run_id, 32, record(run_id, 1))
    worker.repair_outbox()
    assert worker.release_unverified_pressure() == 0
    assert worker.index_one(batch['id']) == 'not_ready'
    assert storage.get(batch['object_key']) == body
    with database.connection() as conn:
        archived = conn.execute('SELECT * FROM ecc_public.batches WHERE id=%s', (batch['id'],)).fetchone()
        assert archived['audit_state'] == 'unverified'
        assert archived['indexed_at'] is None
        assert archived['pressure_released_at'] is not None
        assert conn.execute('SELECT outcome FROM ecc_public.audit_events').fetchone()['outcome'] == 'error'
        assert conn.execute('SELECT trusted_at FROM ecc_public.runs').fetchone()['trusted_at'] is None
        assert conn.execute('SELECT pending_batches,pending_bytes FROM ecc_public.pressure_shards WHERE shard=%s', (run_id % 16,)).fetchone() == {'pending_batches': 0, 'pending_bytes': 0}
    assert service.upload_batch(key, run_id, 32, record(run_id, 1))['cursor'] == 64
    assert worker.release_unverified_pressure() == 0

    # A later successful operator retry must not release the same capacity twice.
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s", (run_id,))
        conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' WHERE id=%s", (batch['id'],))
    assert worker.index_one(batch['id']) == 'indexed'
    assert worker.index_one(batch['id']) == 'already_done'
    with database.connection() as conn:
        assert conn.execute('SELECT pending_batches FROM ecc_public.pressure_shards WHERE shard=%s', (run_id % 16,)).fetchone()['pending_batches'] == 1


def test_dispatch_keeps_sampling_and_first_run_gate(database, aws_resources, settings, monkeypatch):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    trusted_run = service.register(key, str(uuid.uuid4()))['run_id']
    untrusted_run = service.register(key, str(uuid.uuid4()))['run_id']
    for offset in (0, 32, 64):
        service.upload_batch(key, trusted_run, offset, record(trusted_run, offset))
    service.upload_batch(key, untrusted_run, 0, record(untrusted_run, 0))
    with database.connection() as conn:
        conn.execute('UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s', (trusted_run,))
        conn.execute("UPDATE ecc_public.batches SET audit_indices='[0]'::jsonb WHERE run_id=%s AND byte_offset=64", (trusted_run,))
    draws = iter([1, 0, 0])  # One unsampled batch and one selected record.
    monkeypatch.setattr('ecc_challenges.queues.secrets.randbelow', lambda _: next(draws))
    worker = QueueWorker(database, storage, settings, sqs)
    assert worker.dispatch_trusted_audits() == 2
    assert worker.dispatch_trusted_audits() == 0
    with database.connection() as conn:
        rows = conn.execute('SELECT run_id,byte_offset,audit_indices,audit_state,indexed_at FROM ecc_public.batches ORDER BY run_id,byte_offset').fetchall()
    assert [(r['audit_state'], r['audit_indices']) for r in rows[:3]] == [('not_selected', []), ('pending', [0]), ('pending', [0])]
    assert rows[3]['audit_state'] == 'pending' and rows[3]['audit_indices'] is None
    assert all(r['indexed_at'] is None for r in rows)


@pytest.mark.parametrize('suspend', [False, True])
def test_slow_index_does_not_block_upload_or_publish_suspended_run(database, aws_resources, settings, suspend):
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run = service.register(key, str(uuid.uuid4()))['run_id']
    service.upload_batch(key, run, 0, record(run, 0))
    with database.connection() as conn:
        conn.execute('UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s', (run,))
        batch = conn.execute("UPDATE ecc_public.batches SET audit_state='not_selected' RETURNING id").fetchone()['id']
    reached, resume = Event(), Event()
    class Proxy:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, name): return getattr(self.conn, name)
        def execute(self, query, *args, **kwargs):
            result = self.conn.execute(query, *args, **kwargs)
            if query == 'ANALYZE incoming':
                reached.set()
                assert resume.wait(10)
            return result
    class SlowDatabase:
        @contextmanager
        def connection(self):
            with database.connection() as conn:
                yield Proxy(conn)
    worker = QueueWorker(SlowDatabase(), storage, settings, sqs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        indexed = pool.submit(worker.index_one, batch)
        assert reached.wait(5)
        try:
            uploaded = pool.submit(service.upload_batch, key, run, 32, record(run, 1))
            assert uploaded.result(timeout=3)['cursor'] == 64
            if suspend:
                with database.connection() as conn:
                    conn.execute('UPDATE ecc_public.runs SET suspended_at=now() WHERE run_id=%s', (run,))
        finally:
            resume.set()
        assert indexed.result(timeout=5) == ('discarded' if suspend else 'indexed')
    with database.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ecc_public.candidate_points').fetchone()['n'] == (0 if suspend else 1)
        assert (conn.execute('SELECT indexed_at FROM ecc_public.batches WHERE id=%s', (batch,)).fetchone()['indexed_at'] is None) == suspend
