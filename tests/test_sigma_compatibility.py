from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from ecc_challenges.app import create_app
from ecc_challenges.continuations import Continuations
from ecc_challenges.historical import DirectorySink, export_database, load, load_part
from ecc_challenges.protocol import WALK_ID, check_records
from ecc_challenges.queues import QueueWorker
from ecc_challenges.service import Service, Rejected
from test_pipeline import account, checkpoint, record

ROOT = Path(__file__).resolve().parents[1]


def test_real_sigma_reference_mixed_thresholds_and_bounded_continuation(tmp_path):
    if not shutil.which("g++"):
        pytest.fail("g++ is required to verify sigma compatibility")
    binary = tmp_path / "compat-test"
    subprocess.run(["g++", "-O2", "-std=c++17", "-DECC_WALK_TABLE=0", "-DECC_WITNESS=0",
                    "-I" + str(ROOT / "vendor/crypto/ecc2k130/include"),
                    str(ROOT / "verifier/test_sigma_compatibility.cpp"), "-o", str(binary)], check=True)
    result = json.loads(subprocess.check_output([str(binary)], timeout=120))
    assert result["direct_matches_verified"] > 0
    assert result["additional_matches_after_continuation"] > 0
    assert result["old_seeds_stopping_earlier_at_new_threshold"] > 0


def test_registered_threshold_is_immutable_and_checkpoint_bound(database, aws_resources, settings):
    storage, _, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    secret, key = account(service)
    nonce = str(uuid.uuid4())
    client = TestClient(create_app(settings, db=database, storage=storage))
    headers = {"Authorization": "Bearer " + secret, "X-Registration-Id": nonce,
               "X-DP-Weight": "34", "X-Walk-Id": WALK_ID}
    response = client.post("/v1/register", headers=headers)
    assert response.status_code == 201
    run = response.json()["run_id"]
    assert response.json()["dp_weight"] == 34
    assert service.run_status(key, run)["dp_weight"] == 34
    assert client.post("/v1/register", headers={**headers, "X-DP-Weight": "30"}).status_code == 409
    assert client.post("/v1/register", headers={**headers, "X-Walk-Id": "table-walk"}).status_code == 409
    assert client.post("/v1/register", headers={**headers, "X-DP-Weight": "35"}).status_code == 400
    raw = record(run, 0, (1 << 34) - 1)
    service.upload_batch(key, run, 0, raw)
    with pytest.raises(Rejected):
        service.upload_checkpoint(key, run, 32, checkpoint(run))
    bound = bytearray(checkpoint(run))
    struct.pack_into("<i", bound, 40, 34)
    assert service.upload_checkpoint(key, run, 32, bytes(bound))["cursor"] == 32
    run30 = service.register(key, str(uuid.uuid4()), 30)["run_id"]
    with pytest.raises(Rejected):
        service.upload_batch(key, run30, 0, record(run30, 0, (1 << 32) - 1))


def test_database_export_filters_and_keeps_unknown_threshold(database, postgres_dsn, tmp_path):
    with psycopg.connect(postgres_dsn, row_factory=dict_row, autocommit=True) as conn:
        for weight, seed in ((30, 1), (32, 2), (34, 3)):
            conn.execute("INSERT INTO public.distinguished_points(campaign_id,point_key,walk_seed,worker_id) "
                         "VALUES ('ecc2k-130',%s,%s,'old')",
                         (((1 << weight) - 1).to_bytes(24, "little"), bytes([seed])))
        sink = DirectorySink(tmp_path)
        got = export_database(conn, sink, source_id="rho-dp", walk_id=WALK_ID, part_records=1, log=lambda _: None)
        assert got["scanned"] == 3 and got["records"] == 2 and got["parts"] == 2
        assert got["rejected_records"] == {"above_dp32_weight": 1}
        assert all(p["source_dp_weight"] is None for p in sink.sidecars())
        assert export_database(conn, sink, source_id="rho-dp", walk_id=WALK_ID,
                               part_records=1, log=lambda _: None)["skipped_done"] == 2
        with pytest.raises(ValueError):
            export_database(conn, sink, source_id="rho-dp", walk_id="table-walk")
        # Source rows survived both read-only exports. Load into an empty target.
        assert conn.execute("SELECT count(*) AS n FROM public.distinguished_points").fetchone()["n"] == 3
        assert export_database(conn, None, source_id="rho-dp", walk_id=WALK_ID,
                               source_worker_id="absent", write=False, log=lambda _: None)["scanned"] == 0
        filtered = DirectorySink(tmp_path / "worker")
        assert export_database(conn, filtered, source_id="rho-dp", walk_id=WALK_ID,
                               source_worker_id="old", log=lambda _: None)["records"] == 2
        assert {p["worker_id"] for p in filtered.sidecars()} == {"old"}
        conn.execute("DELETE FROM public.distinguished_points")
        assert load(conn, sink, log=lambda _: None)["inserted"] == 2
        assert load(conn, sink, log=lambda _: None)["already_loaded"] == 2
        assert conn.execute("SELECT count(*) AS n FROM public.distinguished_points WHERE source_dp_weight IS NULL").fetchone()["n"] == 2


def test_dp34_index_matches_both_old_dp30_and_dp32(database, postgres_dsn, aws_resources, settings):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run = service.register(key, str(uuid.uuid4()), 34)["run_id"]
    with psycopg.connect(postgres_dsn, row_factory=dict_row, autocommit=True) as conn:
        for weight in (30, 32):
            body = weight.to_bytes(8, "little") + ((1 << weight) - 1).to_bytes(24, "little")
            part = {"records": 1, "part_sha256": hashlib.sha256(body).hexdigest(),
                    "source_key": f"old/{weight}", "worker_id": "old", "source_dp_weight": weight, "walk_id": WALK_ID}
            assert load_part(conn, part, body)["inserted"] == 1
            with pytest.raises(RuntimeError):
                load_part(conn, {**part, "walk_id": "table-walk"}, body)
    raw = record(run, 0, (1 << 30) - 1) + record(run, 1, (1 << 32) - 1)
    service.upload_batch(key, run, 0, raw)
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET trusted_at=now()")
        batch = conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' RETURNING id").fetchone()["id"]
    worker = QueueWorker(database, storage, settings, sqs)
    assert worker.index_one(batch) == "indexed"
    with database.connection() as conn:
        leads = conn.execute("SELECT prior_seed_bytes FROM ecc_public.collision_leads ORDER BY record_index").fetchall()
    assert [bytes(r["prior_seed_bytes"]) for r in leads] == [b"\x1e", b"\x20"]
    jobs = Continuations(worker)
    assert jobs.enqueue(1, 32) == 1
    assert jobs.enqueue(1, 32) == 1  # the second record remains eligible
    assert jobs.enqueue(1, 32) == 0
    assert jobs.enqueue(1, 30) == 1
    with pytest.raises(ValueError):
        jobs.enqueue(101, 32)


def test_continuation_does_not_touch_suspended_or_modified_source(database, aws_resources, settings, tmp_path):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run = service.register(key, str(uuid.uuid4()), 34)["run_id"]
    service.upload_batch(key, run, 0, record(run, 0))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET trusted_at=now()")
        batch = conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' RETURNING id").fetchone()["id"]
    worker = QueueWorker(database, storage, settings, sqs)
    worker.index_one(batch)
    jobs = Continuations(worker)
    jobs.enqueue(1, 32)
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET suspended_at=now()")
    assert jobs.run_one(max_extra_steps=1, timeout_seconds=1, target_weight=32)["state"] == "discarded"
    with database.connection() as conn:
        assert conn.execute("SELECT cursor FROM ecc_public.runs").fetchone()["cursor"] == 32
        assert conn.execute("SELECT count(*) AS n FROM public.distinguished_points").fetchone()["n"] == 0


@pytest.mark.parametrize('outcome', ['bounded', 'timeout', 'complete', 'bad_scalar', 'changed_object'])
def test_continuation_receipt_requires_independent_proof_and_preserves_totals(
        outcome, database, aws_resources, settings, tmp_path, monkeypatch):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    _, key = account(service)
    run = service.register(key, str(uuid.uuid4()), 34)['run_id']
    original = record(run, 0, (1 << 34) - 1)
    extended = record(run, 0, (1 << 32) - 1)
    service.upload_batch(key, run, 0, original)
    with database.connection() as conn:
        conn.execute('UPDATE ecc_public.runs SET trusted_at=now()')
        batch = conn.execute("UPDATE ecc_public.batches SET audit_state='sampled' RETURNING id,object_key").fetchone()
        conn.execute("INSERT INTO public.distinguished_points(campaign_id,point_key,walk_seed,worker_id,source_dp_weight) "
                     "VALUES ('ecc2k-130',%s,%s,'old',32)", (extended[8:], b'\x01'))
    binary = tmp_path / 'compatibility-fixture'
    binary.write_bytes(b'orchestration fixture; arithmetic is tested separately')
    worker = QueueWorker(database, storage, replace(settings, compatibility_binary=str(binary)), sqs)
    worker.index_one(batch['id'])
    jobs = Continuations(worker)
    jobs.enqueue(1, 32)
    calls = []

    def execute(argv, *, input, **kwargs):
        calls.append(argv)
        if argv[1] == 'continue':
            if outcome == 'timeout':
                raise subprocess.TimeoutExpired(argv, 1)
            if outcome == 'bounded':
                return subprocess.CompletedProcess(argv, 3, b'{"state":"bounded"}', b'')
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                'record': extended.hex(), 'target_weight': 32, 'extra_steps': 1}).encode(), b'')
        if argv[1] == 'solve':
            assert argv[2:] == ['32', '32']
            assert input == extended + b'\x01' + bytes(7) + extended[8:]
            return subprocess.CompletedProcess(argv, 0, b'{"verified":true,"scalar":"17"}', b'')
        if argv[1] == '--verify-scalar':
            assert input == b'17\n'
            ok = outcome != 'bad_scalar'
            return subprocess.CompletedProcess(argv, 0, json.dumps({'scalar_match': ok}).encode(), b'')
        assert argv[1:] in (['--dp-weight', '34'], ['--dp-weight', '32'])
        return subprocess.CompletedProcess(argv, 0, b'{}', b'')

    monkeypatch.setattr('ecc_challenges.continuations.subprocess.run', execute)
    if outcome == 'changed_object':
        storage.s3.put_object(Bucket=settings.bucket, Key=batch['object_key'], Body=extended)
    result = jobs.run_one(max_extra_steps=1, timeout_seconds=10, target_weight=32)
    expected = 'bounded' if outcome in ('bounded', 'timeout') else 'unverified' if outcome == 'changed_object' else 'complete'
    assert result['state'] == expected
    assert result['verified_solutions'] == (1 if outcome == 'complete' else 0)
    receipt = json.loads(storage.get(result['receipt']))
    assert receipt['state'] == expected
    if outcome == 'changed_object':
        assert calls == []
    if outcome in ('complete', 'bad_scalar'):
        assert any(argv[1] == '--verify-scalar' for argv in calls)
    with database.connection() as conn:
        assert conn.execute('SELECT cursor FROM ecc_public.runs').fetchone()['cursor'] == 32
        assert conn.execute('SELECT count(*) AS n FROM public.distinguished_points').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM ecc_public.candidate_points').fetchone()['n'] == 1
        assert conn.execute('SELECT state FROM ecc_public.sigma_continuations').fetchone()['state'] == expected
    assert jobs.run_one(max_extra_steps=1, timeout_seconds=10, target_weight=32)['state'] == 'idle'
