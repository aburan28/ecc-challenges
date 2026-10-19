"""Private per-key activity aggregates both worker protocols without double counting."""
import base64
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from ecc_challenges.app import create_app
from ecc_challenges.service import Service
from test_multicurve import admit, dp, platform
from test_pipeline import checkpoint, record


def another_key(service, account_id, label):
    with service.db.connection() as conn:
        conn.execute("UPDATE ecc_public.api_keys SET created_at=now()-interval '2 minutes' WHERE account_id=%s",
                     (account_id,))
    return service.create_key(account_id, label)


def test_key_activity_across_protocols_is_private_and_idempotent(platform):
    generic, key, run, ns, archive, *_ = platform
    legacy = generic.legacy
    owner = key["account_id"]
    intent, _ = admit(platform, [dp(ns, trail=i) for i in (1, 2, 3)])
    assert generic.commit(key, run["run_id"], intent["batch_id"])["duplicate"]
    for size in (2, 3):
        run_id = legacy.register(key, str(uuid4()))["run_id"]
        body = b"".join(record(run_id, i) for i in range(size))
        legacy.upload_batch(key, run_id, 0, body)
        assert legacy.upload_batch(key, run_id, 0, body)["duplicate"]
        legacy.upload_checkpoint(key, run_id, len(body), checkpoint(run_id))

    second = legacy.authenticate(another_key(legacy, owner, "second worker"))
    second_run = legacy.register(second, str(uuid4()))["run_id"]
    legacy.upload_batch(second, second_run, 0, record(second_run, 1))
    another_key(legacy, owner, "never used")
    legacy.upsert_account(999, "other-account")
    foreign = legacy.authenticate(legacy.create_key(999, "private-foreign-label"))
    foreign_run = legacy.register(foreign, str(uuid4()))["run_id"]
    legacy.upload_batch(foreign, foreign_run, 0, record(foreign_run, 1))

    # A later API read is key use, but does not claim new worker progress.
    ping = datetime(2026, 10, 19, 8, 20, 30, tzinfo=timezone(timedelta(hours=-7)))
    used = ping + timedelta(minutes=5)
    with legacy.db.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET last_seen_at=%s WHERE key_id=%s", (ping - timedelta(hours=1), key["id"]))
        conn.execute("UPDATE ecc_public.dp_runs SET last_seen_at=%s WHERE key_id=%s", (ping, key["id"]))
        conn.execute("UPDATE ecc_public.api_keys SET last_used_at=%s WHERE id=%s", (used, key["id"]))
    rows = {row["label"]: row for row in legacy.account_keys(owner)}
    assert set(rows) == {"correctness", "second worker", "never used"}
    mixed = rows["correctness"]
    assert mixed["points_received"] == 8
    assert mixed["last_ping_at"] == ping and mixed["last_ping_at"].utcoffset() == timedelta(0)
    assert mixed["last_used_at"] == used and mixed["last_used_at"].utcoffset() == timedelta(0)
    assert rows["second worker"]["points_received"] == 1
    assert rows["never used"]["points_received"] == 0
    assert rows["never used"]["last_ping_at"] is rows["never used"]["last_used_at"] is None
    assert all("digest" not in row for row in rows.values())
    assert legacy.account_keys(1000) == []
    assert [row["label"] for row in legacy.account_keys(999)] == ["private-foreign-label"]

    # Retiring/revoking a run or key retains its contribution history.
    generic.close_run(key, run["run_id"])
    legacy.revoke_key(owner, key["id"])
    revoked = next(row for row in legacy.account_keys(owner) if row["id"] == key["id"])
    assert revoked["revoked_at"] is not None and revoked["points_received"] == 8
    assert revoked["last_ping_at"] == ping and revoked["last_used_at"] == used


def session_cookie(account_id, settings):
    return TimestampSigner(settings.session_secret).sign(base64.b64encode(json.dumps(
        {"github_id": account_id, "login": "test", "csrf": "test-csrf"}).encode())).decode()


def test_private_dashboard_renders_activity_and_new_key_empty_state(database, aws_resources, settings):
    storage, *_ = aws_resources
    service = Service(database, storage, settings)
    service.upsert_account(1234, "test")
    secret = service.create_key(1234, "home GPU")
    key = service.authenticate(secret)
    run = service.register(key, str(uuid4()))["run_id"]
    with database.connection() as conn:
        conn.execute("UPDATE ecc_public.runs SET total_bytes=%s,last_seen_at=%s WHERE run_id=%s",
                     (12345 * 32, datetime(2026, 10, 19, 13, 14, 15, tzinfo=timezone.utc), run))
        conn.execute("UPDATE ecc_public.api_keys SET created_at=now()-interval '2 minutes',last_used_at=%s WHERE id=%s",
                     (datetime(2026, 10, 19, 13, 20, 21, tzinfo=timezone.utc), key["id"]))
    service.upsert_account(5678, "other")
    service.create_key(5678, "hidden foreign key")
    app = create_app(settings, db=database, storage=storage)
    with TestClient(app, base_url=settings.public_origin) as client:
        assert client.get("/dashboard").status_code == 401
        client.cookies.set("ecc_session", session_cookie(1234, settings))
        response = client.get("/dashboard")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        for text in ("Last ping", "Last used", "Points received", "12,345", "13:14:15 UTC", "13:20:21 UTC"):
            assert text in response.text
        assert 'datetime="2026-10-19T13:14:15+00:00"' in response.text
        assert "hidden foreign key" not in response.text and secret not in response.text
        issued = client.post("/keys", data={"csrf": "test-csrf", "label": "new unused key"})
        assert issued.status_code == 200
        assert 'new unused key' in issued.text and issued.text.count('>Never</span>') == 2
        assert '<td class="num">0</td>' in issued.text
        assert "12,345" in issued.text
