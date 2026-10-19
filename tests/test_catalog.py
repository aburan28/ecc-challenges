from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from psycopg import errors

from ecc_challenges.app import create_app
from ecc_challenges.catalog import BY_ID, CHALLENGES
from ecc_challenges.status import StatusReader


class FakeConnection:
    historical = {"points": 338_034_791, "last_loaded_at": datetime(2026, 10, 17, 21, 4, tzinfo=timezone.utc)}

    @contextmanager
    def transaction(self):
        yield

    def execute(self, statement, params=None):
        if "historical_import_totals" in statement:
            if isinstance(self.historical, Exception):
                raise self.historical
            return SimpleNamespace(fetchone=lambda: self.historical)
        if "ecc_public.runs" in statement:
            return SimpleNamespace(fetchone=lambda: {"total": 3, "recent": 2, "bytes": 320})
        if "ecc_public.pressure_shards" in statement:
            return SimpleNamespace(fetchone=lambda: {"pending_batches": 4, "pending_bytes": 128})
        if "ecc_public.batches" in statement:
            return SimpleNamespace(fetchone=lambda: {"points": 48})
        raise AssertionError(statement)


class FakeDB:
    def __init__(self, historical=FakeConnection.historical):
        self.historical = historical

    @contextmanager
    def connection(self):
        conn = FakeConnection()
        conn.historical = self.historical
        yield conn


def application(db=None):
    settings = SimpleNamespace(database_url="", region="us-west-2", bucket="unused",
                               stream_id="test", github_client_id="test-id",
                               github_client_secret="test-secret",
                               session_secret="s" * 48,
                               public_origin="https://ecc-challenges.com",
                               public_worker_image="example.invalid/image@sha256:" + "a" * 64,
                               public_cpu_worker_image="ghcr.io/aburan28/ecc-challenges-worker-cpu@sha256:" + "b" * 64)
    return create_app(settings, db=db or FakeDB(), storage=object())


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
        assert catalog.json()["cpu_worker_image"].endswith("b" * 64)
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
        assert ecc2k130["historical_points"] == 338_034_791
        assert ecc2k130["points_per_hour"] == 2.0
        # Public runs alone; the imported run is reported separately, never folded in.
        assert ecc2k130["estimated_work_operations"] == 10 * 2**32
        assert ecc2k130["estimated_work_log2"] == 35.32
        assert ecc2k130["expected_operations_log2"] == 60.79
        assert 0 < ecc2k130["expected_work_percent"] < 1e-5
        combined = ecc2k130["including_historical"]
        assert combined["estimated_work_operations"] == (10 + 338_034_791) * 2**32
        assert combined["estimated_work_log2"] == 60.33
        assert abs(combined["expected_work_percent"] - 72.59) < 0.01
        assert abs(combined["estimated_completion_probability_percent"] - 33.89) < 0.01
        assert combined["points_per_hour"] == ecc2k130["points_per_hour"]
        assert combined["hours_to_expected_work"] < ecc2k130["hours_to_expected_work"]
        assert ecc2k130["hours_to_expected_work"] > 0
        assert status["campaigns"][19]["expected_operations_log2"] == 64.92
        assert "expected_work_percent" not in status["campaigns"][19]
        assert status["campaigns"][19]["received_points"] is None
        page = client.get("/status")
        assert page.status_code == 200
        assert b"Live status" in page.content
        assert b"2.0e18" in page.content
        assert b"2<sup>60.8</sup>" in page.content and page.content.count(b'role="progressbar"') == 2
        assert b"Public runs on this gateway" in page.content and b"72.6% with import" in page.content
        assert b"Level II" in client.get("/").content and b"Level Ii" not in client.get("/").content


def test_challenge_pages_and_catalog_links_cover_all_24_instances():
    from ecc_challenges.multicurve.instances import get_instance

    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        for page_path in ("/", "/status"):
            page = client.get(page_path)
            assert page.status_code == 200
            for challenge in CHALLENGES:
                assert f'href="/challenges/{challenge.id}"'.encode() in page.content

        for challenge in CHALLENGES:
            response = client.get(f"/challenges/{challenge.id}")
            assert response.status_code == 200
            assert challenge.public()["name"] in response.text
            assert get_instance(challenge.id).instance_sha256 in response.text
            assert "/status#challenges" in response.text

        assert client.get("/challenges/not-a-challenge").status_code == 404


def test_html_errors_robots_and_share_metadata():
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        home = client.get("/")
        assert b'property="og:title"' in home.content
        assert b'href="/docs"' in home.content
        assert b'href="https://github.com/' not in home.content
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
    assert ecc2k130["estimated_work_operations"] == 5 * 2**32
    assert ecc2k130["including_historical"]["estimated_work_operations"] == (5 + 90) * 2**32


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


def test_status_page_counts_imported_points_with_the_gateway_split_shown():
    with TestClient(application()) as client:
        status = client.get("/v1/status").json()
        row = status["campaigns"][6]
        assert row["id"] == "ecc2k-130" and row["received_points"] == 10
        assert row["historical_points"] == 338_034_791
        assert status["total_archived_points"] == 10
        assert status["total_historical_points"] == 338_034_791
        assert status["historical_imported_at"] == "2026-10-17T21:04:00+00:00"
        page = client.get("/status").text
        assert "338,034,801" in page and "338,034,791 imported · 10 uploaded here" in page
        assert "last loaded 2026-10-17" in page
        assert "338,034,801" in client.get("/").text


def test_status_page_keeps_working_when_import_totals_are_unreadable():
    with TestClient(application(FakeDB(historical=errors.InsufficientPrivilege("denied")))) as client:
        status = client.get("/v1/status")
        assert status.status_code == 200
        assert status.json()["total_historical_points"] is None
        assert status.json()["campaigns"][6]["historical_points"] is None
        assert status.json()["campaigns"][6]["including_historical"] is None
        page = client.get("/status").text
        assert ">10<small>stored durably</small>" in page
        assert "Counts cover this gateway only." in page


def test_status_page_keeps_catalog_when_database_is_unavailable():
    class UnavailableDB(FakeDB):
        @contextmanager
        def connection(self):
            raise RuntimeError("status database offline")
            yield

    with TestClient(application(UnavailableDB())) as client:
        page = client.get("/status")
        assert page.status_code == 200
        assert "Live counts are unavailable" in page.text
        assert "ECC2K-130" in page.text and "2.0e18" in page.text
        assert 'role="progressbar"' not in page.text
        assert client.get("/v1/status").status_code == 503


def test_account_totals_include_runs_outside_display_limit(database, aws_resources, settings):
    import uuid
    from ecc_challenges.service import Service
    from ecc_challenges.protocol import RECORD_BYTES
    service = Service(database, aws_resources[0], settings)
    service.upsert_account(17, "many-runs")
    key = service.authenticate(service.create_key(17, "rig"))
    for count in (1, 2, 3):
        run_id = service.register(key, str(uuid.uuid4()))["run_id"]
        with database.connection() as conn:
            conn.execute("UPDATE ecc_public.runs SET total_bytes=%s WHERE run_id=%s",
                         (count * RECORD_BYTES, run_id))
            conn.execute("""
                INSERT INTO ecc_public.batches(run_id,byte_offset,byte_length,records,sha256,object_key)
                VALUES (%s,0,%s,%s,%s,%s)
            """, (run_id, count * RECORD_BYTES, count, "c" * 64, f"total/{count}"))
    activity = service.account_runs(17, limit=1)
    assert len(activity["runs"]) == 1
    assert activity["total_runs"] == activity["active_runs"] == 3
    assert activity["points"] == activity["points_last_hour"] == 6
    assert activity["points_per_hour"] == round(6 / 24, 1)


def test_one_line_installers_are_served_as_text():
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        unix = client.get("/install.sh")
        assert unix.status_code == 200 and unix.headers["content-type"].startswith("text/plain")
        assert unix.text.startswith("#!/bin/sh") and "deploy/native/install.sh" in unix.text
        windows = client.get("/install.ps1")
        assert windows.status_code == 200 and "deploy\\native\\install.ps1" in windows.text
        assert client.get("/install.bat").status_code == 404


def test_dashboard_shows_setup_for_each_system():
    from starlette.requests import Request
    from ecc_challenges.app import TEMPLATES, client_os

    def request(agent):
        return Request({"type": "http", "method": "GET", "path": "/dashboard", "query_string": b"",
                        "headers": [(b"user-agent", agent.encode())], "session": {"github_id": 1},
                        "scheme": "https", "server": ("ecc-challenges.com", 443)})

    windows = request("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    assert client_os(windows) == "windows"
    assert client_os(request("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)")) == "macos"
    assert client_os(request("Mozilla/5.0 (X11; Linux x86_64)")) == "linux"
    for enabled in (False, True):
        page = TEMPLATES.get_template("dashboard.html").render(
            request=windows, login="me", keys=[], csrf="x", issued=None, challenges=CHALLENGES,
            worker_image="example.invalid/image@sha256:" + "a" * 64, multicurve_enabled=enabled,
            cpu_worker_image="ghcr.io/aburan28/ecc-challenges-worker-cpu@sha256:" + "b" * 64,
            selected_campaign="eccp-131", client_os="windows", activity=None, names={})
        assert 'id="os-windows" checked' in page and 'id="os-linux" checked' not in page
        assert "curl -fsSL https://ecc-challenges.com/install.sh |" in page
        assert "irm https://ecc-challenges.com/install.ps1 | iex" in page
        assert ("Not accepting work yet" in page) is not enabled
        assert ("ECC_CAMPAIGN=eccp-131 sh" in page) is enabled
        assert ("CPU, with Docker (x86-64 or ARM64)" in page) is enabled
        assert ("ecc-challenges-worker-cpu@sha256:" in page) is enabled
