from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ecc_challenges.app import create_app
from ecc_challenges.catalog import BY_ID, CHALLENGES
from ecc_challenges.status import StatusReader


class FakeConnection:
    def execute(self, statement, params=None):
        if "has_table_privilege" in statement:
            return SimpleNamespace(fetchone=lambda: {"ok": True})
        if "historical_import_parts" in statement:
            return SimpleNamespace(fetchone=lambda: {"points": 6})
        if "ecc_public.runs" in statement:
            return SimpleNamespace(fetchone=lambda: {"total": 3, "recent": 2, "bytes": 320})
        if "ecc_public.pressure_shards" in statement:
            return SimpleNamespace(fetchone=lambda: {"pending_batches": 4, "pending_bytes": 128})
        if "ecc_public.batches" in statement:
            return SimpleNamespace(fetchone=lambda: {"points": 48})
        raise AssertionError(statement)


class FakeDB:
    @contextmanager
    def connection(self):
        yield FakeConnection()


def application():
    settings = SimpleNamespace(database_url="", region="us-west-2", bucket="unused",
                               stream_id="test", github_client_id="test-id",
                               github_client_secret="test-secret",
                               session_secret="s" * 48,
                               public_origin="https://ecc-challenges.com",
                               public_worker_image="example.invalid/image@sha256:" + "a" * 64)
    return create_app(settings, db=FakeDB(), storage=object())


def test_certicom_catalog_has_all_published_names_and_exact_campaign_boundary():
    assert len(CHALLENGES) == 24
    assert len({item.id for item in CHALLENGES}) == 24
    assert sum(item.solved_in_2009 for item in CHALLENGES) == 10
    assert BY_ID["ecc2-353"].field_bits == 359
    assert BY_ID["eccp-191"].field_bits == 191
    from ecc_challenges.multicurve.instances import all_instances
    assert all(BY_ID[c.id].field_bits == c.bits for c in all_instances())
    assert {item.id for item in CHALLENGES if item.contribution != "not_integrated"} == {"ecc2k-130"}
    assert BY_ID["ecc2k-130"].estimated_operations_2009 == "2.0e18"
    assert all(item.estimated_operations_2009 for item in CHALLENGES)
    assert BY_ID["ecc2k-95"].estimated_operations_2009 == "1.8e13"


def test_catalog_site_status_and_fail_closed_registration():
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        catalog = client.get("/v1/campaigns")
        assert catalog.status_code == 200
        assert len(catalog.json()["campaigns"]) == 24
        assert client.get("/v1/config?campaign=ecc2k-130").json()["campaign"] == "ecc2k-130"
        assert client.get("/v1/config?campaign=eccp-131").status_code == 409
        assert client.get("/v1/config?campaign=not-a-challenge").status_code == 404
        refused = client.post("/v1/register", headers={"X-Campaign-Id": "eccp-131"})
        assert refused.status_code == 409
        assert client.get("/").status_code == 200
        assert b"ECCp-359" in client.get("/").content
        status = client.get("/v1/status").json()
        assert status["campaigns"][6]["received_points"] == 10
        assert status["campaigns"][6]["pending_batches"] == 4
        ecc2k130 = status["campaigns"][6]
        assert ecc2k130["historical_points"] == 6
        assert ecc2k130["points_per_hour"] == 2.0
        assert ecc2k130["estimated_work_operations"] == 16 * 2**32
        assert ecc2k130["estimated_work_log2"] == 36.0
        assert ecc2k130["expected_operations_log2"] == 60.79
        assert 0 < ecc2k130["expected_work_percent"] < 1e-5
        assert 0 <= ecc2k130["estimated_completion_probability_percent"] < 1e-5
        assert ecc2k130["hours_to_expected_work"] > 0
        assert status["campaigns"][19]["expected_operations_log2"] == 64.92
        assert "expected_work_percent" not in status["campaigns"][19]
        assert status["campaigns"][19]["received_points"] is None
        page = client.get("/status")
        assert page.status_code == 200
        assert b"Live status" in page.content
        assert b"2.0e18" in page.content
        assert b"2<sup>60.8</sup>" in page.content and b'role="progressbar"' in page.content
        assert b"Level II" in client.get("/").content and b"Level Ii" not in client.get("/").content


def test_html_errors_robots_and_share_metadata():
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        home = client.get("/")
        assert b'property="og:title"' in home.content
        assert b"github.com/aburan28/ecc-challenges" in home.content
        missing = client.get("/no-such-page")
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("text/html")
        assert b"Page not found" in missing.content
        assert client.get("/v1/no-such-route").json() == {"detail": "Not Found"}
        signed_out = client.get("/dashboard")
        assert signed_out.status_code == 401 and b"/auth/github" in signed_out.content
        robots = client.get("/robots.txt")
        assert robots.status_code == 200 and "Disallow: /dashboard" in robots.text


def test_progress_estimate_and_collision_probability():
    from ecc_challenges.status import progress
    expected = 2.0e18
    half = progress({32: int(expected / 2**33)}, {32: 24 * 1000}, "2.0e18")
    assert abs(half["expected_work_percent"] - 50) < 0.01
    # 1 - exp(-pi/16) for half the expected work.
    assert abs(half["estimated_completion_probability_percent"] - 17.83) < 0.01
    assert half["points_per_hour"] == 1000
    assert abs(half["operations_per_second_log2"] - math.log2(1000 * 2**32 / 3600)) < 0.01
    assert progress({}, {}, "2.0e18")["estimated_work_log2"] is None
    assert progress({32: int(expected / 2**31)}, {}, "2.0e18")["hours_to_expected_work"] is None
    assert progress({32: int(expected / 2**31)}, {32: 1}, "2.0e18")["hours_to_expected_work"] == 0


def test_display_filters():
    from ecc_challenges.app import duration, percent, pow2
    assert str(pow2(60.79)) == "2<sup>60.8</sup>" and pow2(None) == "—"
    assert percent(0) == "0.0%" and percent(0.001) == "<0.01%" and percent(1.234) == "1.23%"
    assert percent(72.46) == "72.5%"
    assert duration(0.5) == "30 minutes" and duration(30) == "1.2 days" and duration(24 * 365.25 * 200) == "200 years"


def test_account_runs_and_status_against_postgres(database, aws_resources, settings):
    import uuid
    from ecc_challenges.service import Service
    from ecc_challenges.protocol import RECORD_BYTES
    storage = aws_resources[0]
    service = Service(database, storage, settings)
    service.upsert_account(7, "runner")
    service.upsert_account(8, "someone-else")
    key = service.authenticate(service.create_key(7, "rig"))
    other = service.authenticate(service.create_key(8, "theirs"))
    run_id = service.register(key, str(uuid.uuid4()))["run_id"]
    service.register(other, str(uuid.uuid4()))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET total_bytes=%s WHERE run_id=%s", (5 * RECORD_BYTES, run_id))
        for offset, (records, age) in enumerate(((2, "10 minutes"), (3, "3 hours"), (4, "2 days"))):
            conn.execute("""
                INSERT INTO ecc_public.batches(run_id,byte_offset,byte_length,records,sha256,object_key,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,now()-%s::interval)
            """, (run_id, offset * 4096, records * 32, records, "a" * 64, f"test/{offset}", age))
        conn.execute("""
            INSERT INTO ecc_public.historical_import_parts(source_key,part_sha256,records,inserted,conflicts)
            VALUES ('part-0',%s,100,90,0)
        """, ("b" * 64,))
    activity = service.account_runs(7)
    assert [run["id"] for run in activity["runs"]] == [str(run_id)]
    run = activity["runs"][0]
    assert run["state"] == "active" and run["key_label"] == "rig"
    assert (run["points"], run["last_hour"], run["last_day"]) == (5, 2, 5)
    assert run["points_per_hour"] == round(5 / 24, 1)
    assert [batch["records"] for batch in activity["recent"]] == [2, 3, 4]
    assert activity["points_last_hour"] == 2 and activity["active_runs"] == 1
    assert service.account_runs(8)["recent"] == []
    snapshot = StatusReader(database).read()
    ecc2k130 = snapshot["campaigns"][6]
    assert ecc2k130["historical_points"] == 90
    assert ecc2k130["points_per_hour"] == round(5 / 24, 1)
    assert ecc2k130["estimated_work_operations"] == (5 + 90) * 2**32


def test_status_reader_caches_database_snapshot():
    class CountingDB(FakeDB):
        calls = 0

        @contextmanager
        def connection(self):
            self.calls += 1
            yield FakeConnection()

    db = CountingDB()
    reader = StatusReader(db)
    assert reader.read() is reader.read()
    assert db.calls == 1


def test_provider_environment_key_is_removed_before_cuda_launch(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "worker"))
    from client import read_api_key

    key = "ecc1_" + "a" * 24 + "_" + "b" * 43
    monkeypatch.setenv("ECC_API_KEY", key)
    monkeypatch.setenv("ECC_API_KEY_FILE", str(tmp_path / "missing"))
    assert read_api_key() == key
    assert "ECC_API_KEY" not in os.environ
