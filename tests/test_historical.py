from __future__ import annotations

import hashlib
from math import log2
import json
import secrets
import uuid

import boto3
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from ecc_challenges import historical
from ecc_challenges.queues import QueueWorker
from ecc_challenges.service import Service

SOURCE = "ecc2k130-590183823895"


def dp(seed: int, point: int) -> bytes:
    return seed.to_bytes(8, "little") + point.to_bytes(24, "little")


def orbit_key(slot: int, body: bytes, offset: int = 0) -> str:
    return f"dp/slot-{slot:05d}/{uuid.UUID(int=slot).hex}-{offset:016d}-{hashlib.sha256(body).hexdigest()}.bin"


@pytest.fixture
def source_bucket(aws_resources):
    s3 = boto3.client("s3", region_name="us-west-2")
    s3.create_bucket(Bucket=SOURCE, CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
    return s3


@pytest.fixture
def conn(postgres_dsn, database):
    with psycopg.connect(postgres_dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


def put(s3, key: str, body: bytes, marker: dict | None = None) -> None:
    s3.put_object(Bucket=SOURCE, Key=key, Body=body)
    if marker is not None:
        s3.put_object(Bucket=SOURCE, Key=key[:-4] + ".bin.json", Body=json.dumps(marker).encode())


def test_check_object_refuses_bad_objects_and_drops_bad_records():
    good = dp(7 << 48 | 1, 0b11)
    with pytest.raises(historical.SourceRejected, match="sha256 in key"):
        historical.check_object(orbit_key(1, b"other"), good, None)
    with pytest.raises(historical.SourceRejected, match="whole 32-byte"):
        historical.check_object("dp/slot-00001/1789311001-0000000000000000.bin", good[:31], None)
    with pytest.raises(historical.SourceRejected, match="witness"):
        historical.check_object("dp/slot-00001/1789311001-0000000000000000.bin", b"ECC2KDP2" + bytes(24), None)
    with pytest.raises(historical.SourceRejected, match="record count"):
        historical.check_object(orbit_key(1, good), good, json.dumps({"sha256": hashlib.sha256(good).hexdigest(), "records": 2}).encode())

    public = dp(50000 << 48 | 1, 0b11)   # reserved for the public gateway
    odd = dp(7 << 48 | 2, 0b111)         # odd weight is not a canonical orbit key
    dp34 = dp(7 << 48 | 3, (1 << 34) - 1)  # an early DP34 point: valid, never matchable
    body = good + public + odd + dp34
    checked = historical.check_object("dp/slot-00001/1789311001-0000000000000000.bin", body, None)
    assert checked.records == 1 and checked.body == good
    assert checked.rejected == {"public_run_range": 1, "not_orbit_key": 1, "above_dp32_weight": 1}


def test_runner_delta_keys_are_checked_against_their_name():
    body = dp(7 << 48 | 1, 0b11) + dp(7 << 48 | 2, 0b101)
    sha = hashlib.sha256(body).hexdigest()
    good = f"dp/slot-00007/{sha}-{64:016d}-{128:016d}.bin"
    assert historical.check_object(good, body, None).records == 2
    with pytest.raises(historical.SourceRejected, match="sha256 in key"):
        historical.check_object(f"dp/slot-00007/{'0' * 64}-{64:016d}-{128:016d}.bin", body, None)
    with pytest.raises(historical.SourceRejected, match="offsets in key"):
        historical.check_object(f"dp/slot-00007/{sha}-{0:016d}-{128:016d}.bin", body, None)


def test_export_load_is_idempotent_and_keeps_historical_collisions(source_bucket, conn, tmp_path):
    a = dp(7 << 48 | 1, 0b11) + dp(7 << 48 | 2, 0b101)
    b = dp(9 << 48 | 5, 0b101) + dp(9 << 48 | 6, 0b1001)   # 0b101 again, other seed
    put(source_bucket, "dp/slot-00002/1789311001-0000000000000000.bin", a)
    put(source_bucket, orbit_key(140, b), b, {"sha256": hashlib.sha256(b).hexdigest(), "records": 2})
    source_bucket.put_object(Bucket=SOURCE, Key="dp/README", Body=b"not a dp object")

    dry = historical.export(source_bucket, SOURCE, None, write=False, log=lambda _: None)
    assert dry["objects"] == 2 and dry["records"] == 4 and dry["ignored_keys"] == 1

    sink = historical.open_sink(str(tmp_path / "parts"))
    assert historical.export(source_bucket, SOURCE, sink, log=lambda _: None)["objects"] == 2
    assert historical.export(source_bucket, SOURCE, sink, log=lambda _: None)["skipped_done"] == 2

    first = historical.load(conn, sink, log=lambda _: None)
    assert first == {"parts": 2, "already_loaded": 0, "records": 4, "inserted": 3, "conflicts": 1}
    assert historical.load(conn, sink, log=lambda _: None)["already_loaded"] == 2

    rows = conn.execute("SELECT point_key,walk_seed,worker_id FROM public.distinguished_points "
                        "ORDER BY point_key").fetchall()
    assert len(rows) == 3
    # Same encodings as the original ingester and as queues._seed_bytes.
    row = next(r for r in rows if bytes(r["point_key"]) == (0b11).to_bytes(24, "little"))
    assert bytes(row["walk_seed"]) == (7 << 48 | 1).to_bytes(8, "big").lstrip(b"\0")
    assert row["worker_id"] == "dp-slot-00002-1789311001-0000000000000000.bin"
    # Whichever witness loaded first is stored; the other is kept as a conflict.
    conflict = conn.execute("SELECT * FROM ecc_public.historical_conflicts").fetchone()
    assert {bytes(conflict["walk_seed"]), bytes(conflict["stored_seed"])} == {
        historical.seed_bytes(9 << 48 | 5), historical.seed_bytes(7 << 48 | 2)}


def test_load_refuses_a_part_that_changed(source_bucket, conn, tmp_path):
    body = dp(7 << 48 | 1, 0b11)
    put(source_bucket, "dp/slot-00002/1789311001-0000000000000000.bin", body)
    sink = historical.open_sink(str(tmp_path / "parts"))
    historical.export(source_bucket, SOURCE, sink, log=lambda _: None)
    part = next((tmp_path / "parts" / "parts").glob("*.bin"))
    part.write_bytes(dp(7 << 48 | 1, 0b101))
    with pytest.raises(RuntimeError, match="does not match its sidecar"):
        historical.load(conn, sink, log=lambda _: None)
    assert conn.execute("SELECT count(*) AS n FROM public.distinguished_points").fetchone()["n"] == 0


def test_backmatch_writes_the_lead_index_one_missed(database, aws_resources, settings, source_bucket, conn, tmp_path):
    storage, sqs, _, _, _ = aws_resources
    service = Service(database, storage, settings)
    service.upsert_account(1234, "contributor")
    key = service.authenticate(service.create_key(1234, "test GPU"))
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    point = 0b110
    service.upload_batch(key, run_id, 0, dp(run_id << 48 | 1, 0b11) + dp(run_id << 48 | 2, point))
    with database.connection() as c:
        c.execute("UPDATE ecc_public.runs SET trusted_at=now() WHERE run_id=%s", (run_id,))
        c.execute("UPDATE ecc_public.batches SET audit_state='sampled'")
        batch = c.execute("SELECT id FROM ecc_public.batches").fetchone()["id"]
    assert QueueWorker(database, storage, settings, sqs).index_one(batch) == "indexed"

    # The historical point arrives after the public batch was indexed.
    old = dp(7 << 48 | 3, point)
    put(source_bucket, "dp/slot-00002/1789311001-0000000000000000.bin", old)
    sink = historical.open_sink(str(tmp_path / "parts"))
    historical.export(source_bucket, SOURCE, sink, log=lambda _: None)
    historical.load(conn, sink, log=lambda _: None)
    assert conn.execute("SELECT count(*) AS n FROM ecc_public.collision_leads").fetchone()["n"] == 0

    assert historical.backmatch(conn, storage, log=lambda _: None)["leads"] == 1
    assert historical.backmatch(conn, storage, log=lambda _: None) == {"matches": 0, "leads": 0, "same_seed": 0}
    lead = conn.execute("SELECT * FROM ecc_public.collision_leads").fetchone()
    assert lead["prior_source"] == "campaign" and lead["batch_id"] == batch and lead["record_index"] == 1
    assert bytes(lead["prior_seed_bytes"]) == historical.seed_bytes(7 << 48 | 3)
    assert int(lead["seed"]) == run_id << 48 | 2


def test_reconcile_reports_workers_short_of_the_source():
    out = historical.reconcile({"dp-a": 5, "dp-b": 2}, {"dp-a": 5, "dp-b": 1, "dp-c": 4})
    assert out["source_total"] == 7 and out["imported_total"] == 10
    assert out["short"] == {"dp-b": 2} and out["workers_ahead_of_source"] == 1


def test_identical_source_bodies_keep_separate_sidecars_and_ledger_rows(source_bucket, conn, tmp_path):
    body = dp(7 << 48 | 1, 0b11)
    put(source_bucket, "dp/slot-00002/1789311001-0000000000000000.bin", body)
    put(source_bucket, "dp/slot-00003/1789311002-0000000000000000.bin", body)
    sink = historical.open_sink(str(tmp_path / "parts"))
    assert historical.export(source_bucket, SOURCE, sink, log=lambda _: None)["objects"] == 2
    assert len(sink.done_ids()) == 2
    assert historical.export(source_bucket, SOURCE, sink, log=lambda _: None)["skipped_done"] == 2
    assert historical.load(conn, sink, log=lambda _: None) == {
        "parts": 2, "already_loaded": 0, "records": 2, "inserted": 1, "conflicts": 0}
    assert conn.execute("SELECT count(*) AS n FROM ecc_public.historical_import_parts").fetchone()["n"] == 2


def test_s3_sink_resumes_by_listing(aws_resources, source_bucket, conn):
    s3 = boto3.client("s3", region_name="us-west-2")
    body = dp(7 << 48 | 1, 0b11)
    put(source_bucket, "dp/slot-00002/1789311001-0000000000000000.bin", body)
    sink = historical.open_sink("s3://ecc-test-corpus/historical-import/test", s3)
    assert historical.export(source_bucket, SOURCE, sink, log=lambda _: None)["objects"] == 1
    assert historical.export(source_bucket, SOURCE, sink, log=lambda _: None)["skipped_done"] == 1
    assert historical.load(conn, sink, log=lambda _: None)["inserted"] == 1


def ledger_row(conn, source_key: str, inserted: int) -> None:
    conn.execute("""
        INSERT INTO ecc_public.historical_import_parts(source_key,part_sha256,records,inserted,conflicts)
        VALUES (%s,%s,%s,%s,0)
    """, (source_key, hashlib.sha256(source_key.encode()).hexdigest(), inserted, inserted))


def test_status_reports_imported_points_beside_gateway_uploads(database, conn):
    from ecc_challenges.status import StatusReader

    empty = StatusReader(database, ttl_seconds=0).read()
    assert empty["total_historical_points"] == 0 and empty["historical_imported_at"] is None
    ledger_row(conn, "dp/slot-00001/a.bin", 300)
    ledger_row(conn, "dp/slot-00002/b.bin", 38)
    snapshot = StatusReader(database, ttl_seconds=0).read()
    row = next(c for c in snapshot["campaigns"] if c["id"] == "ecc2k-130")
    assert row["historical_points"] == 338 and row["received_points"] == 0
    assert snapshot["total_historical_points"] == 338
    assert snapshot["total_archived_points"] == 0
    assert snapshot["historical_imported_at"].startswith("20")
    # 338 imported DP32 points on the sigma walk, at 2^28.41 steps each.
    assert row["estimated_work_log2"] is None
    assert row["including_historical"]["estimated_work_log2"] == round(log2(338) + 28.409072957536694, 2)


def test_status_preserves_each_namespace_walk_expectation(database, conn):
    import math
    from psycopg.types.json import Jsonb
    from ecc_challenges.multicurve.codec import Namespace
    from ecc_challenges.status import StatusReader

    # Equal work in distinct point and negation namespaces contributes separate
    # birthday terms, using n and n/2 respectively, regardless of today's default.
    points, bits = 10**9, 34
    for version in (2, 4):
        ns = Namespace("eccp-131", bits, version)
        conn.execute("INSERT INTO ecc_public.dp_namespaces(id,campaign_id,manifest,record_bytes) VALUES (%s,%s,%s,%s)",
                     (ns.id, ns.campaign, Jsonb(ns.public()), ns.record_bytes))
        conn.execute("""INSERT INTO ecc_public.dp_namespace_stats(namespace_id,shard,admitted_records)
                        VALUES (%s,0,%s)""", (ns.id, points))
    row = next(c for c in StatusReader(database, ttl_seconds=0, multicurve_enabled=True).read()["campaigns"]
               if c["id"] == "eccp-131")
    work = points * 2**bits
    n = ns.instance.n
    expectation2, expectation4 = math.sqrt(math.pi * n / 2), math.sqrt(math.pi * n / 4)
    share = (work / expectation2)**2 + (work / expectation4)**2
    assert math.isclose(row["expected_work_percent"], 100 * math.sqrt(share))
    assert math.isclose(row["estimated_completion_probability_percent"],
                        -100 * math.expm1(-math.pi / 4 * share))
    assert row["estimated_work_operations"] == 2 * work


def test_status_estimates_v2_work_per_pinned_dp_setting(database, conn):
    from psycopg.types.json import Jsonb

    from ecc_challenges.multicurve.codec import namespace
    from ecc_challenges.status import StatusReader

    # Two namespaces for one curve, pinned to different DP bits: 3*2^20 + 5*2^18 steps.
    for dp_bits, verified in ((20, 3), (18, 5)):
        ns = namespace("eccp-131", dp_bits)
        conn.execute("INSERT INTO ecc_public.dp_namespaces(id,campaign_id,manifest,record_bytes) VALUES (%s,%s,%s,%s)",
                     (ns.id, "eccp-131", Jsonb(ns.public()), ns.record_bytes))
        conn.execute("""INSERT INTO ecc_public.dp_namespace_stats(namespace_id,shard,admitted_records,verified_records)
                        VALUES (%s,0,%s,%s)""", (ns.id, verified, verified))
    row = next(c for c in StatusReader(database, ttl_seconds=0, multicurve_enabled=True).read()["campaigns"]
               if c["id"] == "eccp-131")
    assert row["generic_verified_points"] == 8
    assert row["estimated_work_log2"] == round(log2(3 * 2**20 + 5 * 2**18), 2)
    assert row["dp_bits"] == [18, 20]
    assert row["expected_operations_log2"] == 64.92  # the negation-map walk halves the group
    assert 0 < row["estimated_completion_probability_percent"] < 1e-10


def test_status_survives_a_web_role_without_the_totals_grant(postgres_dsn, database, conn):
    """Web deployed before the bootstrap regrant: the snapshot loses only the import totals."""
    from ecc_challenges.db import Database
    from ecc_challenges.status import StatusReader

    ledger_row(conn, "dp/slot-00001/a.bin", 5)
    password = secrets.token_hex(16)    # CI's PostgreSQL uses password authentication
    probe_dsn = psycopg.conninfo.make_conninfo(postgres_dsn, user="status_probe", password=password)
    conn.execute("DROP ROLE IF EXISTS status_probe")
    conn.execute(sql.SQL("CREATE ROLE status_probe LOGIN PASSWORD {}").format(sql.Literal(password)))
    try:
        conn.execute("GRANT USAGE ON SCHEMA ecc_public TO status_probe")
        conn.execute("""
            GRANT SELECT ON ecc_public.runs,ecc_public.batches,ecc_public.pressure_shards,ecc_public.dp_namespaces,
              ecc_public.dp_runs,ecc_public.dp_namespace_stats,ecc_public.dp_batches TO status_probe
        """)
        probe = Database(probe_dsn, max_size=1)
        probe.open()
        try:
            # Multicurve on, so statements run after the refused one inside the same transaction.
            snapshot = StatusReader(probe, ttl_seconds=0, multicurve_enabled=True).read()
        finally:
            probe.close()
        assert snapshot["total_historical_points"] is None
        assert snapshot["historical_imported_at"] is None
        assert all(c["historical_points"] is None for c in snapshot["campaigns"])
        assert snapshot["compute_ready"] == 24 and snapshot["total_archived_points"] == 0
        conn.execute("GRANT SELECT ON ecc_public.historical_import_totals TO status_probe")
        probe = Database(probe_dsn, max_size=1)
        probe.open()
        try:
            assert StatusReader(probe, ttl_seconds=0).read()["total_historical_points"] == 5
        finally:
            probe.close()
    finally:
        conn.execute("DROP OWNED BY status_probe")
        conn.execute("DROP ROLE status_probe")
