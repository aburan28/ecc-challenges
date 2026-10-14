"""STARK verification modes (needs the eccstark binary: cargo build --release in stark/)."""

import json
import secrets
from pathlib import Path

import pytest
from conftest import admin_headers, issue_key
from test_pool import drain_audits, linear_sweep_shares, submit

from eccpool.ec import Curve
from eccpool.stark import StarkEngine, find_binary
from eccpool.walk import run_walk
from eccpool.worker import Client, answer_challenges, share_json, spec_from_job

pytestmark = pytest.mark.skipif(find_binary() is None, reason="eccstark binary not built")

GL32 = Curve.from_dict(json.loads(
    (Path(__file__).resolve().parent.parent / "challenges" / "gl32.json").read_text())["curve"])
FAST = dict(shares_per_minute=1e6, share_burst=10_000, requests_per_minute=1e6)


@pytest.fixture(scope="module")
def eng():
    e = StarkEngine(procs=2)
    yield e
    e.close()


def make_job(c, mode, k, dp_bits=8, **kw):
    Q = GL32.mul(k, GL32.G)
    r = c.post("/admin/jobs", headers=admin_headers(), json={
        "name": f"gl32-{mode}", "dp_bits": dp_bits, "curve": GL32.to_dict(),
        "Q": [hex(Q[0]), hex(Q[1])], "verification": mode, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def native_shares(eng, job, token, count):
    spec = spec_from_job(job)
    key_id = token.split("_")[1]
    mode = job["spec"]["verification"]
    out = []
    while len(out) < count:
        nonce = secrets.token_hex(8)
        w = eng.work(spec, key_id, nonce, prove=(mode == "stark"))
        if w is None:
            continue
        s = {"nonce": nonce, "length": w["length"], "dp": w["dp"], "a": w["a"], "b": w["b"],
             "checkpoints": [] if mode == "stark" else w["checkpoints"]}
        if mode == "stark":
            s["proof"] = w["proof"]
        out.append(s)
    return out


def test_native_walk_matches_python_spec(eng):
    """The Rust walker must produce exactly the shares the Python spec defines."""
    Q = GL32.mul(424242, GL32.G)
    from eccpool.walk import WalkSpec
    spec = WalkSpec(GL32, Q, "xcheck", dp_bits=8)
    for i in range(5):
        res = run_walk(spec, "key", f"n{i}")
        w = eng.work(spec, "key", f"n{i}", prove=False)
        assert share_json(res) == {"nonce": f"n{i}", "length": w["length"], "dp": w["dp"],
                                   "a": w["a"], "b": w["b"], "checkpoints": w["checkpoints"]}


def test_stark_job_needs_goldilocks(make_app, curve):
    c = make_app()
    Q = curve.mul(5, curve.G)
    r = c.post("/admin/jobs", headers=admin_headers(), json={
        "name": "x", "dp_bits": 6, "curve": curve.to_dict(), "Q": [hex(Q[0]), hex(Q[1])],
        "verification": "stark"})
    assert r.status_code == 400 and "Goldilocks" in r.json()["detail"]


def test_stark_mode_accepts_only_proven_shares(make_app, eng):
    c = make_app(max_strikes=100)
    t1, h1 = issue_key(c, "honest", **FAST)
    t2, h2 = issue_key(c, "thief", **FAST)
    job = make_job(c, "stark", 12345)
    good = native_shares(eng, job, t1, 3)
    r = submit(c, h1, job["job_id"], good).json()["results"]
    assert [x["status"] for x in r] == ["accepted"] * 3
    me = c.get("/v1/me", headers=h1).json()
    assert me["credit"]["verified"] == 3 * 256      # verified on arrival, no audits needed

    no_proof = dict(native_shares(eng, job, t1, 1)[0])
    del no_proof["proof"]
    assert submit(c, h1, job["job_id"], [no_proof]).json()["results"][0]["code"] == "missing_proof"

    # Another key replaying a share (proof included): the proof is for the
    # honest key's start point, so it does not verify for the thief's.
    stolen = dict(native_shares(eng, job, t1, 1)[0])
    assert submit(c, h2, job["job_id"], [stolen]).json()["results"][0]["code"] == "bad_proof"

    # A proof of a different walk does not transfer.
    a, b = native_shares(eng, job, t1, 2)
    swapped = dict(a, proof=b["proof"])
    assert submit(c, h1, job["job_id"], [swapped]).json()["results"][0]["code"] == "bad_proof"


def test_stark_mode_cheater_gets_nothing(make_app, eng):
    c = make_app(max_strikes=5)
    token, h = issue_key(c, "cheater", **FAST)
    job = make_job(c, "stark", 999)
    fakes = linear_sweep_shares(job, 8)
    for f in fakes:
        f["checkpoints"] = []
        f["proof"] = "AAAA"
    r = submit(c, h, job["job_id"], fakes).json()["results"]
    assert {x["code"] for x in r} == {"bad_proof"}
    assert len(r) == 5                                 # suspended at the 5th strike
    stats = c.get(f"/admin/keys/{token.split('_')[1]}", headers=admin_headers()).json()
    assert stats["status"] == "suspended" and stats["credit"]["earned"] == 0


def test_stark_mode_solves(make_app, eng):
    c = make_app()
    k = secrets.randbelow(GL32.n - 1) + 1
    workers = [issue_key(c, f"w{i}", **FAST) for i in range(2)]
    job = make_job(c, "stark", k, dp_bits=8)
    for _ in range(200):
        done = False
        for token, h in workers:
            res = submit(c, h, job["job_id"], native_shares(eng, job, token, 16)).json()["results"]
            assert all(x["status"] == "accepted" or x.get("code") == "job_solved" for x in res), res
            done = done or any(x.get("solved") or x.get("code") == "job_solved" for x in res)
        if done:
            break
    j = c.get(f"/v1/jobs/{job['job_id']}", headers=workers[0][1]).json()
    assert j["status"] == "solved" and int(j["solution"], 16) == k


def _client(c, token):
    cl = Client("http://testserver", token)
    cl.http = c                                        # route through the TestClient
    cl.http.headers["Authorization"] = f"Bearer {token}"
    return cl


def test_challenge_mode_honest_worker_passes(make_app, eng):
    c = make_app()
    token, h = issue_key(c, "honest", **FAST)
    job = make_job(c, "stark-challenge", 777, challenge_rate=1.0)
    res = submit(c, h, job["job_id"], native_shares(eng, job, token, 6)).json()["results"]
    assert all(x["status"] == "accepted" and x["challenge"] for x in res)
    me = c.get("/v1/me", headers=h).json()
    assert me["credit"]["pending_proof"] == 6 * 256 and me["credit"]["earned"] == 0
    pend = c.get("/v1/challenges", headers=h).json()["challenges"]
    assert len(pend) == 6
    answer_challenges(_client(c, token), spec_from_job(job), pend)
    me = c.get("/v1/me", headers=h).json()
    assert me["status"] == "active" and me["audits_passed"] == 6
    assert me["credit"]["verified"] == 6 * 256 and me["credit"]["pending_proof"] == 0


def test_challenge_mode_cheater_is_revoked(make_app, eng):
    c = make_app()
    token, h = issue_key(c, "cheater", **FAST)
    key_id = token.split("_")[1]
    job = make_job(c, "stark-challenge", 4321, challenge_rate=1.0)
    spec = spec_from_job(job)
    honest = native_shares(eng, job, token, 2)
    fakes = linear_sweep_shares(job, 3)
    res = submit(c, h, job["job_id"], honest + fakes).json()["results"]
    assert all(x["status"] == "accepted" for x in res)
    fake_ch = [x["challenge"] for x in res[2:]][0]
    # The best a cheater can do: prove the honest walk from the challenged
    # anchor. It does not land on the submitted checkpoint, so it fails.
    share = fakes[0]
    _, _, start = spec.start(key_id, share["nonce"])
    anchors = [start] + [(int(x, 16), int(y, 16)) for x, y in share["checkpoints"]]
    seg = fake_ch["segment"]
    last = seg == spec.n_segments(share["length"]) - 1
    _, proof = eng.prove(spec, anchors[seg], fake_ch["to_step"] - fake_ch["from_step"], False)
    r = c.post(f"/v1/challenges/{fake_ch['challenge_id']}/proof", headers=h, json={"proof": proof})
    assert r.json()["status"] == "failed", (r.json(), last)
    stats = c.get(f"/admin/keys/{key_id}", headers=admin_headers()).json()
    assert stats["status"] == "revoked"
    assert stats["credit"]["earned"] == 0 and stats["credit"]["verified"] == 0


def test_unanswered_challenge_expires_and_revokes(make_app, eng):
    c = make_app()
    token, h = issue_key(c, "lazy", **FAST)
    job = make_job(c, "stark-challenge", 55, challenge_rate=1.0)
    submit(c, h, job["job_id"], native_shares(eng, job, token, 2))
    pool = c.app.state.pool
    assert pool.expire_challenges(now=pool.db.one("SELECT MAX(deadline) d FROM audits")["d"] + 1) == 2
    assert c.get("/v1/me", headers=h).status_code == 403


def test_challenge_belongs_to_key(make_app, eng):
    c = make_app()
    t1, h1 = issue_key(c, "a", **FAST)
    _, h2 = issue_key(c, "b", **FAST)
    job = make_job(c, "stark-challenge", 66, challenge_rate=1.0)
    res = submit(c, h1, job["job_id"], native_shares(eng, job, t1, 1)).json()["results"]
    cid = res[0]["challenge"]["challenge_id"]
    assert c.post(f"/v1/challenges/{cid}/proof", headers=h2, json={"proof": "AAAA"}).status_code == 404
    assert c.post(f"/v1/challenges/{cid}/proof", headers=h1, json={"proof": "!!"}).status_code == 400
    drain_audits(c)
