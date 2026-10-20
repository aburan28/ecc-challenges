from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from ecc_challenges.app import create_app
from ecc_challenges.catalog import BY_ID, CHALLENGES
from ecc_challenges.status import StatusReader
from ecc_challenges.work import SIGMA_DP_LOG2


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
        if "AS dp_weight" in statement:
            return SimpleNamespace(fetchall=lambda: [{"dp_weight": 32, "bytes": 320, "recent": 48}])
        if "ecc_public.runs" in statement:
            return SimpleNamespace(fetchone=lambda: {"total": 3, "recent": 2, "bytes": 320})
        if "ecc_public.pressure_shards" in statement:
            return SimpleNamespace(fetchone=lambda: {"pending_batches": 4, "pending_bytes": 128})
        raise AssertionError(statement)


class FakeDB:
    def __init__(self, historical=FakeConnection.historical):
        self.historical = historical

    @contextmanager
    def connection(self):
        conn = FakeConnection()
        conn.historical = self.historical
        yield conn


def application(db=None, multicurve_enabled=False):
    settings = SimpleNamespace(database_url="", region="us-west-2", bucket="unused",
                               stream_id="test", github_client_id="test-id", multicurve_enabled=multicurve_enabled,
                               github_client_secret="test-secret",
                               session_secret="s" * 48,
                               public_origin="https://ecc-challenges.com",
                               public_worker_image="example.invalid/image@sha256:" + "a" * 64,
                               public_cpu_worker_image="ghcr.io/aburan28/ecc-challenges-worker-cpu@sha256:" + "b" * 64)
    return create_app(settings, db=db or FakeDB(),
                      storage=SimpleNamespace(s3=None, bucket="unused", stream_id="test"))


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
        config = client.get("/v1/config?campaign=ecc2k-130").json()
        assert config["campaign"] == "ecc2k-130"
        assert config["recommended_dp_weight"] == 34
        assert config["supported_dp_weights"] == [30, 32, 34]
        assert config["dp_weight"] == 32  # clients without threshold support
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
        # DP32 on the sigma walk is Hamming weight <= 32: one step in 2^28.41, not 2^32.
        assert ecc2k130["estimated_work_operations"] == 10 * 2.0 ** -SIGMA_DP_LOG2
        assert ecc2k130["estimated_work_log2"] == 31.73
        assert ecc2k130["expected_operations_log2"] == 60.81
        assert 0 < ecc2k130["expected_work_percent"] < 1e-5
        combined = ecc2k130["including_historical"]
        assert combined["estimated_work_operations"] == (10 + 338_034_791) * 2.0 ** -SIGMA_DP_LOG2
        assert combined["estimated_work_log2"] == 56.74
        assert abs(combined["expected_work_percent"] - 5.96) < 0.01
        assert abs(combined["estimated_completion_probability_percent"] - 0.28) < 0.01
        assert combined["points_per_hour"] == ecc2k130["points_per_hour"]
        assert combined["hours_to_expected_work"] < ecc2k130["hours_to_expected_work"]
        assert ecc2k130["hours_to_expected_work"] > 0
        # Unopened rows still say what their walk would need, beside Certicom's figure.
        assert status["campaigns"][19]["expected_operations_log2"] == 65.42
        assert status["campaigns"][19]["estimated_operations_2009_log2"] == 64.92
        assert "expected_work_percent" not in status["campaigns"][19]
        assert status["campaigns"][19]["received_points"] is None
        page = client.get("/status")
        assert page.status_code == 200
        assert b"Live status" in page.content
        assert b"2.0e18" in page.content
        assert b"2<sup>60.8</sup>" in page.content and page.content.count(b'role="progressbar"') == 2
        card = page.text.split('aria-label="ECC2K-130 progress"', 1)[1].split("</article>", 1)[0]
        assert card.index("Including imported points") < card.index("5.96%") < card.index("Public uploads only")
        assert '<span class="mono">5.96%</span>' in page.text
        assert '&lt;0.01% public uploads only' in page.text
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


def test_work_is_estimated_from_stored_points_as_powers_of_two():
    from math import comb, log2

    from ecc_challenges.multicurve.instances import get_instance
    from ecc_challenges.work import Walk, progress, sigma_expected_log2

    # Even-weight 131-bit normal-basis x with weight <= 32: 2^-28.41 of them.
    assert abs(SIGMA_DP_LOG2 - (log2(sum(comb(131, w) for w in range(0, 33, 2))) - 130)) < 1e-12
    assert round(SIGMA_DP_LOG2, 2) == -28.41
    # The sigma walk's expectation matches Certicom's 2.0e18 to within rounding.
    assert abs(sigma_expected_log2(get_instance("ecc2k-130").n) - BY_ID["ecc2k-130"].estimated_operations_log2) < 0.02
    # Exactly the expected work gives rho a 1 - exp(-pi/4) chance.
    at_expected = progress([Walk(30, 2**30, 0, -30.0, 60.0)], 60.0, 24)
    assert at_expected["estimated_work_log2"] == 60.0 and at_expected["expected_work_percent"] == 100
    assert round(at_expected["estimated_completion_probability_percent"], 1) == 54.4
    # Two independent walks on one curve: work adds, and so do their squared shares.
    both = progress([Walk(30, 2**29, 0, -30.0, 60.0), Walk(34, 2**25, 0, -34.0, 60.0)], 60.0, 24)
    assert both["estimated_work_log2"] == 60.0 and round(both["expected_work_percent"], 2) == 70.71
    assert both["dp_bits"] == [30, 34]
    # Each walk reaches the shared expectation together: 1/sqrt(2) of its own at the same pace.
    paced = progress([Walk(30, 2**29, 2**10, -30.0, 60.0), Walk(30, 2**29, 2**10, -30.0, 60.0)], 60.0, 1)
    assert abs(paced["hours_to_expected_work"] - (2**60 / 2**0.5 - 2**59) / 2**40) < 1

    with TestClient(application()) as client:
        status = client.get("/v1/status").json()
        row = status["campaigns"][6]["including_historical"]
        # 10 uploaded + 338,034,791 imported DP32 points.
        assert row["estimated_work_log2"] == 56.74 and row["expected_operations_log2"] == 60.81
        assert round(row["expected_work_percent"], 1) == 6.0
        assert round(row["estimated_completion_probability_percent"], 2) == 0.28
        assert status["total_estimated_work_log2"] == 31.73
        assert status["total_estimated_work_log2_including_historical"] == 56.74
        for path in ("/", "/status", "/challenges/ecc2k-130"):
            assert "2<sup>56.7</sup>" in client.get(path).text
        assert "0.28%" in client.get("/challenges/ecc2k-130").text


def test_pollard_rho_page_explains_the_walk_estimate_and_kernels():
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        page = client.get("/pollard-rho")
        assert page.status_code == 200 and page.headers["cache-control"] == "private, max-age=300"
        for anchor in ("problem", "rho", "parallel", "automorphisms", "walk", "estimate", "kernels", "checks"):
            assert f'id="{anchor}"' in page.text and f'href="#{anchor}"' in page.text
        assert "clmad.lo.u64" in page.text and "Itoh–Tsujii" in page.text
        # The live figure for ECC2K-130 comes from the same snapshot as /status.
        assert "338,034,801 stored points" in page.text and "2<sup>56.74</sup>" in page.text
        assert 'href="/pollard-rho"' in client.get("/").text
    with TestClient(application(FakeDB(historical=errors.InsufficientPrivilege("denied")))) as client:
        assert client.get("/pollard-rho").status_code == 200


def test_progress_estimate_and_collision_probability():
    from ecc_challenges.work import Walk, progress
    expected = 2.0e18
    # A walk whose points need 32 zero bits: 2^32 steps a point.
    def walk(points, recent):
        return [Walk(32, points, recent, -32.0, math.log2(expected))]
    half = progress(walk(int(expected / 2**33), 24 * 1000), math.log2(expected), 24)
    assert abs(half["expected_work_percent"] - 50) < 0.01
    # 1 - exp(-pi/16) for half the expected work.
    assert abs(half["estimated_completion_probability_percent"] - 17.83) < 0.01
    assert half["points_per_hour"] == 1000
    assert abs(half["operations_per_second_log2"] - math.log2(1000 * 2**32 / 3600)) < 0.01
    assert abs(half["hours_to_expected_work"] - expected / 2 / (1000 * 2**32)) < 1
    assert progress([], math.log2(expected), 24)["estimated_work_log2"] is None
    assert progress(walk(int(expected / 2**31), 0), math.log2(expected), 24)["hours_to_expected_work"] is None
    assert progress(walk(int(expected / 2**31), 1), math.log2(expected), 24)["hours_to_expected_work"] == 0


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
    assert ecc2k130["estimated_work_operations"] == 5 * 2.0 ** -SIGMA_DP_LOG2
    assert ecc2k130["including_historical"]["estimated_work_operations"] == (5 + 90) * 2.0 ** -SIGMA_DP_LOG2


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
        page = client.get("/status").text
        assert ">10<small>stored durably</small>" in page
        assert "Counts cover this gateway only." in page


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


def test_sigma_thresholds_share_work_before_collision_probability():
    from ecc_challenges.work import Walk, progress, sigma_dp_log2
    walks = [Walk(weight, 1000, 24, sigma_dp_log2(weight), 60.81, "sigma")
             for weight in (30, 32, 34)]
    estimate = progress(walks, 60.81, 24)
    steps = sum(1000 * 2 ** -sigma_dp_log2(w) for w in (30, 32, 34))
    share = steps / 2 ** 60.81
    assert math.isclose(estimate["expected_work_percent"], 100 * share)
    assert math.isclose(estimate["estimated_completion_probability_percent"],
                        -100 * math.expm1(-math.pi / 4 * share ** 2))
    assert estimate["dp_bits"] == [30, 32, 34]
    assert sigma_dp_log2(30) < sigma_dp_log2(32) < sigma_dp_log2(34)
    assert estimate["points_per_hour"] == 3


def test_status_uses_each_sigma_runs_stored_threshold(database, aws_resources, settings):
    import uuid
    from ecc_challenges.service import Service
    from ecc_challenges.work import sigma_dp_log2
    service = Service(database, aws_resources[0], settings)
    service.upsert_account(99, "threshold-test")
    key = service.authenticate(service.create_key(99, "rig"))
    with database.connection() as conn:
        conn.execute("ALTER TABLE ecc_public.runs ADD COLUMN IF NOT EXISTS dp_weight smallint NOT NULL DEFAULT 32")
    for weight, points in ((30, 10), (32, 20), (34, 30)):
        run_id = service.register(key, str(uuid.uuid4()))["run_id"]
        with database.connection() as conn:
            conn.execute("UPDATE ecc_public.runs SET dp_weight=%s,total_bytes=%s WHERE run_id=%s",
                         (weight, points * 32, run_id))
            conn.execute("""INSERT INTO ecc_public.batches
                (run_id,byte_offset,byte_length,records,sha256,object_key)
                VALUES (%s,0,%s,%s,%s,%s)""",
                (run_id, points * 32, points, "a" * 64, f"threshold/{weight}"))
    row = StatusReader(database).read()["campaigns"][6]
    expected = sum(points * 2 ** -sigma_dp_log2(weight)
                   for weight, points in ((30, 10), (32, 20), (34, 30)))
    assert row["received_points"] == 60
    assert row["dp_bits"] == [30, 32, 34]
    assert math.isclose(row["estimated_work_operations"], expected)
    assert row["points_per_hour"] == 2.5


@pytest.mark.parametrize("enabled", [False, True])
def test_dashboard_threshold_selector_generates_isolated_compatible_commands(monkeypatch, enabled):
    import base64
    import json
    from itsdangerous import TimestampSigner
    from ecc_challenges.service import Service

    monkeypatch.setattr(Service, "account_keys", lambda *args: [])
    run_queries = []
    def account_runs(_service, account_id, *, include_generic):
        run_queries.append((account_id, include_generic))
        return None
    monkeypatch.setattr(Service, "account_runs", account_runs)
    app = application(multicurve_enabled=enabled)
    session = TimestampSigner("s" * 48).sign(base64.b64encode(json.dumps(
        {"github_id": 1, "login": "test", "csrf": "test"}).encode())).decode()
    with TestClient(app, base_url="https://ecc-challenges.com") as client:
        client.cookies.set("ecc_session", session)
        for weight in (None, 30, 32, 34):
            path = "/dashboard?campaign=ecc2k-130" + (f"&dp_weight={weight}" if weight else "")
            page = client.get(path)
            assert page.status_code == 200
            assert f'<option value="{weight or 34}" selected>' in page.text
            assert "DP34 — recommended" in page.text
            assert f"DP_WEIGHT={weight or 34}\n" in page.text
            assert "-e ECC_PROTOCOL=1" in page.text
            assert '-e ECC_DP_WEIGHT="$DP_WEIGHT"' in page.text
            assert 'ecc-data-$CAMPAIGN-dp$DP_WEIGHT-$GPU_ID:/data' in page.text
            assert "Your runners" in page.text
            assert "Run activity is unavailable right now" in page.text
        assert run_queries and all(item == (1, enabled) for item in run_queries)
        assert client.get("/dashboard?dp_weight=36").status_code == 400
        if enabled:
            other = client.get("/dashboard?campaign=eccp-131").text
            assert 'id="dp-weight"' not in other
            assert "ECC_DP_WEIGHT" not in other
            assert "-e ECC_PROTOCOL=2" in other
            assert 'ecc-data-$CAMPAIGN-$GPU_ID:/data' in other


@pytest.mark.parametrize("mode", ["imported", "public_only", "unavailable"])
def test_dashboard_progress_uses_selected_challenge_and_preserves_private_activity(monkeypatch, mode):
    import base64
    import json
    from itsdangerous import TimestampSigner
    from ecc_challenges.service import Service

    snapshot = StatusReader(FakeDB()).read()
    sigma = snapshot["campaigns"][6]
    other = dict(sigma, id="eccp-131", including_historical=None,
                 historical_points=0, expected_work_percent=1.25)
    snapshot["campaigns"] = [sigma, other]
    def read(_reader):
        if mode == "unavailable":
            raise RuntimeError("snapshot unavailable")
        return snapshot
    monkeypatch.setattr(StatusReader, "read", read)
    monkeypatch.setattr(Service, "account_keys", lambda *args: [])
    monkeypatch.setattr(Service, "account_runs", lambda *args, **kwargs: None)
    session = TimestampSigner("s" * 48).sign(base64.b64encode(json.dumps(
        {"github_id": 1, "login": "test", "csrf": "test"}).encode())).decode()
    campaign = "eccp-131" if mode == "public_only" else "ecc2k-130"
    with TestClient(application(multicurve_enabled=True), base_url="https://ecc-challenges.com") as client:
        client.cookies.set("ecc_session", session)
        page = client.get("/dashboard?campaign=" + campaign)
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        progress = page.text.split('aria-labelledby="challenge-progress"', 1)[1].split('</section>', 1)[0]
        assert 'href="/challenges/' + campaign + '"' in progress
        assert 'Iterations per second' in progress
        assert 'estimated from public uploads, 24 h average' in progress
        from ecc_challenges.app import pow2
        rate = '—' if mode == 'unavailable' else str(pow2(sigma['operations_per_second_log2']))
        assert '<dt>Iterations per second</dt><dd>' + rate in progress
        if mode == "imported":
            assert '<span class="progress-primary">5.96%</span>' in progress
            assert '&lt;0.01% public uploads only' in progress
            assert progress.index('progress-primary') < progress.index('progress-public')
        elif mode == "public_only":
            assert '<span class="progress-primary">1.25%</span>' in progress
            assert 'Including imported points' not in progress and 'progress-public' not in progress
        else:
            assert 'Progress unavailable' in progress
        assert 'Your runners' in page.text and 'Run the worker' in page.text
