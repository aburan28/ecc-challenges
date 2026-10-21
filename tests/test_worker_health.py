"""Run-scoped health, protocol-correct rates and private detail pages."""
from dataclasses import replace
import math
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ecc_challenges.app import create_app
from ecc_challenges.service import Service
from ecc_challenges.status import StatusReader
from ecc_challenges.work import sigma_dp_log2
from test_key_activity import session_cookie
from test_multicurve import admit, dp, platform
from test_pipeline import record


def test_each_worker_has_independent_health_and_private_details(database, aws_resources, settings):
    service = Service(database, aws_resources[0], settings)
    service.upsert_account(12, "owner")
    secret = service.create_key(12, "shared key")
    key = service.authenticate(secret)
    first, second = [service.register(key, str(uuid4()))["run_id"] for _ in range(2)]
    service.upsert_account(13, "outsider")
    foreign_secret = service.create_key(13, "other")
    app = create_app(settings, db=database, storage=aws_resources[0])
    with TestClient(app, base_url=settings.public_origin) as client:
        route = f"/dashboard/workers/v1/{first}"
        assert client.get(route).status_code == 401
        client.cookies.set("ecc_session", session_cookie(12, settings))
        assert "Reporting unavailable" in client.get(route).text
        payload = {"protocol": "v1", "run_id": str(first), "state": "computing",
                   "version": "test", "platform": "Linux", "backend": "cuda"}
        headers = {"Authorization": "Bearer " + secret}
        assert client.post("/v1/heartbeat", headers=headers, json=payload).status_code == 204
        runs = {row["id"]: row for row in service.account_runs(12)["runs"]}
        assert runs[str(first)]["health_state"] == "Computing"
        assert runs[str(second)]["health_state"] == "Reporting unavailable"
        assert runs[str(first)]["points"] == 0 and runs[str(first)]["last_batch_at"] is None
        response = client.get(route)
        assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
        assert "No batches received yet" in response.text and secret not in response.text
        for state, label in (("upload_retry", "Upload trouble"), ("backpressure", "Upload backlog")):
            assert client.post("/v1/heartbeat", headers=headers, json={**payload, "state": state}).status_code == 204
            assert label in client.get(route).text
        with database.connection() as conn:
            conn.execute("UPDATE ecc_public.runs SET last_ping_at=now()-interval '181 seconds' WHERE run_id=%s", (first,))
        assert "Disconnected" in client.get(route).text
        assert client.post("/v1/heartbeat", headers={"Authorization": "Bearer " + foreign_secret}, json=payload).status_code == 404
        for invalid in ({**payload, "state": "invented"}, {**payload, "secret": "never store"}, [],
                        {**payload, "run_id": "9999999999999999999999999"}, {**payload, "version": "x" * 81}):
            assert client.post("/v1/heartbeat", headers=headers, json=invalid).status_code == 400
        client.cookies.set("ecc_session", session_cookie(13, settings))
        assert client.get(route).status_code == 404
        assert client.get("/dashboard/workers/v1/not-an-id").status_code == 404
        assert client.get("/dashboard/workers/v2/not-a-uuid").status_code == 404
        service.revoke_key(12, key["id"])
        assert client.post("/v1/heartbeat", headers=headers, json=payload).status_code == 401


@pytest.mark.parametrize("weight", [30, 32, 34])
def test_sigma_rate_matches_campaign_and_excludes_rejected_batches(database, aws_resources, settings, weight):
    service = Service(database, aws_resources[0], settings)
    service.upsert_account(12, "owner")
    key = service.authenticate(service.create_key(12, "worker"))
    run = service.register(key, str(uuid4()), weight)["run_id"]
    service.upload_batch(key, run, 0, record(run, 1))
    service.upload_batch(key, run, 32, record(run, 2))
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.batches SET audit_state='quarantined' WHERE byte_offset=32")
    activity = service.account_runs(12)["runs"][0]
    campaign = next(r for r in StatusReader(database).read()["campaigns"] if r["id"] == "ecc2k-130")
    expected = round(math.log2(2 ** -sigma_dp_log2(weight) / 86400), 2)
    assert activity["dp_bits"] == weight
    assert activity["operations_per_second_log2"] == campaign["operations_per_second_log2"] == expected
    detail = service.worker_detail(12, "v1", str(run), include_generic=False)
    assert detail["failed"] == 1 and detail["pending"] == 1
    assert detail["verification_state"] == "Needs attention"
    checks = campaign["verification"]
    assert checks["failed_batches"] == 1 and checks["oldest_pending_at"]
    assert checks["last_checked_at"] is None and checks["sampled_batches"] == 0


def test_generic_rate_health_and_owner_lookup(platform):
    generic, key, run, ns, *_ = platform
    admit(platform, [dp(ns, trail=1)])
    service = generic.legacy
    payload = {"protocol": "v2", "run_id": str(run["run_id"]), "state": "computing"}
    service.record_heartbeat(key, payload)
    detail = service.worker_detail(key["account_id"], "v2", str(run["run_id"]), include_generic=True)
    assert detail["health_state"] == "Computing" and detail["pending"] == 1
    assert detail["operations_per_second_log2"] == round(ns.dp_bits - math.log2(86400), 2)
    assert service.account_runs(999, include_generic=True)["runs"] == []
    generic.close_run(key, run["run_id"])
    assert service.worker_detail(key["account_id"], "v2", str(run["run_id"]), include_generic=True)["health_state"] == "Closed"
    from ecc_challenges.service import Rejected
    with pytest.raises(Rejected):
        service.record_heartbeat(key, payload)


def test_campaign_survives_oauth_without_open_redirect(database, aws_resources, settings, monkeypatch):
    settings = replace(settings, multicurve_enabled=True)
    app = create_app(settings, db=database, storage=aws_resources[0])
    async def token(request): return {"access_token": "test"}
    class Profile:
        def raise_for_status(self): pass
        def json(self): return {"id": 12, "login": "owner"}
    async def profile(*args, **kwargs): return Profile()
    monkeypatch.setattr(app.state.oauth.github, "authorize_access_token", token)
    monkeypatch.setattr(app.state.oauth.github, "get", profile)
    with TestClient(app, base_url=settings.public_origin) as client:
        page = client.get("/challenges/eccp-131")
        assert '/auth/github?campaign=eccp-131' in page.text
        assert client.get("/auth/github?campaign=https://evil.invalid", follow_redirects=False).status_code == 400
        assert client.get("/auth/github?campaign=eccp-131", follow_redirects=False).status_code == 302
        callback = client.get("/auth/callback", follow_redirects=False)
        assert callback.status_code == 303 and callback.headers["location"] == "/dashboard?campaign=eccp-131"
        page = client.get(callback.headers["location"])
        assert 'value="eccp-131" selected' in page.text
        assert '/assets/live.js' in page.text
        assert "script-src 'self'" in page.headers["content-security-policy"]
        asset = client.get("/assets/live.js")
        assert asset.status_code == 200 and 'text/javascript' in asset.headers['content-type']
