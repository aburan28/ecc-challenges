from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import log2
import uuid

from fastapi.testclient import TestClient

from ecc_challenges.leaderboard import LeaderboardReader, rank
from ecc_challenges.work import sigma_dp_log2

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def accounts(**visible):
    return {index: {"login": login, "visible": shown, "suspended": False}
            for index, (login, shown) in enumerate(visible.items(), start=1)}


def test_ranking_compares_estimated_operations_not_points():
    people = accounts(alice=True, bob=True)
    # Bob uploads ten times the points, but on a 20-bit threshold; Alice's
    # 30-bit points each stand for 1,024 times as many steps.
    board = rank(people, [], [(1, "eccp-131", 30, 10, 1, NOW), (2, "eccp-131", 20, 100, 1, NOW)])
    assert [entry["login"] for entry in board["entries"]] == ["alice", "bob"]
    assert board["entries"][0]["operations_log2"] == round(log2(10 * 2 ** 30), 2)


def test_sigma_points_use_the_weight_threshold():
    board = rank(accounts(carol=True), [(1, 34, 8, 2, NOW)], [])
    entry = board["entries"][0]
    assert entry["challenges"] == ["ecc2k-130"] and entry["runs"] == 2
    assert entry["operations_log2"] == round(log2(8 * 2.0 ** -sigma_dp_log2(34)), 2)


def test_solved_challenges_and_suspended_accounts_do_not_count():
    people = accounts(dave=True, erin=True)
    people[2]["suspended"] = True
    board = rank(people, [], [(1, "ecc2-79", 16, 1000, 1, NOW), (1, "ecc2-131", 16, 5, 1, NOW),
                              (2, "eccp-131", 30, 50, 1, NOW)])
    assert [entry["login"] for entry in board["entries"]] == ["dave"]
    assert board["entries"][0]["challenges"] == ["ecc2-131"]
    assert board["entries"][0]["points"] == 5


def test_hidden_accounts_are_only_counted_in_the_aggregate():
    board = rank(accounts(frank=True, secret=False), [], [(1, "eccp-131", 20, 1, 1, NOW),
                                                         (2, "eccp-131", 20, 3, 1, NOW)])
    assert [entry["login"] for entry in board["entries"]] == ["frank"]
    assert board["hidden"] == {"accounts": 1, "operations_log2": round(log2(3 * 2 ** 20), 2), "points": 3}
    assert board["total_operations_log2"] == round(log2(4 * 2 ** 20), 2)
    assert "secret" not in repr(board)


def test_leaderboard_against_postgres(database, aws_resources, settings):
    from ecc_challenges.app import create_app
    from ecc_challenges.service import Service
    from ecc_challenges.protocol import RECORD_BYTES

    service = Service(database, aws_resources[0], settings)
    for github_id, login in ((11, "listed-high"), (12, "listed-low"), (13, "hidden-person"),
                             (14, "suspended-run")):
        service.upsert_account(github_id, login)
    assert service.leaderboard_visible(11) is False
    service.set_leaderboard_visible(11, True)
    service.set_leaderboard_visible(12, True)
    service.set_leaderboard_visible(14, True)
    runs = {}
    for github_id, points in ((11, 50), (12, 7), (13, 20), (14, 999)):
        key = service.authenticate(service.create_key(github_id, "rig"))
        runs[github_id] = service.register(key, str(uuid.uuid4()))["run_id"]
        with database.connection() as conn:
            conn.execute("UPDATE ecc_public.runs SET total_bytes=%s WHERE run_id=%s",
                         (points * RECORD_BYTES, runs[github_id]))
    with database.connection() as conn:
        # Ten of listed-high's points failed their check; run 14 is suspended.
        conn.execute("""
            INSERT INTO ecc_public.batches(run_id,byte_offset,byte_length,records,sha256,object_key,audit_state)
            VALUES (%s,0,%s,10,%s,'test/failed','failed')
        """, (runs[11], 10 * RECORD_BYTES, "c" * 64))
        conn.execute("UPDATE ecc_public.runs SET suspended_at=now() WHERE run_id=%s", (runs[14],))

    board = LeaderboardReader(database, multicurve_enabled=True).read()
    assert [(entry["login"], entry["points"]) for entry in board["entries"]] == [("listed-high", 40),
                                                                                ("listed-low", 7)]
    assert board["hidden"]["accounts"] == 1 and board["hidden"]["points"] == 20

    client = TestClient(create_app(settings, db=database, storage=aws_resources[0]))
    page = client.get("/leaderboard")
    assert page.status_code == 200
    assert "listed-high" in page.text and "listed-low" in page.text
    assert "hidden-person" not in page.text and "suspended-run" not in page.text
    assert "1 contributor not listed" in page.text
    assert page.text.index("listed-high") < page.text.index("listed-low")

    service.set_leaderboard_visible(11, False)
    assert service.leaderboard_visible(11) is False


def test_dashboard_toggle_needs_csrf_and_updates_the_listing(database, aws_resources, settings):
    from ecc_challenges.app import create_app
    from ecc_challenges.service import Service
    from test_key_activity import session_cookie

    service = Service(database, aws_resources[0], settings)
    service.upsert_account(21, "toggler")
    app = create_app(settings, db=database, storage=aws_resources[0])
    with TestClient(app, base_url=settings.public_origin) as client:
        client.cookies.set("ecc_session", session_cookie(21, settings))
        dashboard = client.get("/dashboard").text
        assert 'id="leaderboard"' in dashboard and "List me on the leaderboard" in dashboard
        refused = client.post("/account/leaderboard", data={"csrf": "wrong", "visible": "1"},
                              follow_redirects=False)
        assert refused.status_code in (400, 403) and service.leaderboard_visible(21) is False
        listed = client.post("/account/leaderboard", data={"csrf": "test-csrf", "visible": "1"},
                             follow_redirects=False)
        assert listed.status_code == 303 and listed.headers["location"] == "/dashboard#leaderboard"
        assert service.leaderboard_visible(21) is True
        assert "Remove me from the leaderboard" in client.get("/dashboard").text


def test_relative_times_are_singular_for_one_unit():
    from ecc_challenges.app import ago

    now = datetime.now(timezone.utc)
    assert ago(now - timedelta(seconds=70)) == "1 minute ago"
    assert ago(now - timedelta(minutes=7)) == "7 minutes ago"
    assert ago(now - timedelta(hours=1, minutes=1)) == "1 hour ago"
    assert ago(now - timedelta(hours=1, minutes=30)) == "1.5 hours ago"
