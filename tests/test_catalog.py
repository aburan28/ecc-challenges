from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ecc_challenges.app import create_app
from ecc_challenges.catalog import BY_ID, CHALLENGES
from ecc_challenges.status import StatusReader


class FakeConnection:
    def execute(self, statement):
        if "ecc_public.runs" in statement:
            return SimpleNamespace(fetchone=lambda: {"total": 3, "recent": 2, "bytes": 320})
        if "ecc_public.pressure_shards" in statement:
            return SimpleNamespace(fetchone=lambda: {"pending_batches": 4, "pending_bytes": 128})
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
        assert status["campaigns"][6]["estimated_completion_probability_percent"] is None
        assert status["campaigns"][19]["received_points"] is None
        page = client.get("/status")
        assert page.status_code == 200
        assert b"Live status" in page.content
        assert b"2.0e18" in page.content
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
