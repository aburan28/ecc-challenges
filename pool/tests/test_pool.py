"""End-to-end and adversarial tests against the HTTP API."""

import secrets

from conftest import admin_headers, create_job, issue_key

from eccpool.walk import WalkResult, run_walk
from eccpool.worker import share_json, spec_from_job


def honest_shares(job: dict, key_token: str, count: int) -> list[dict]:
    spec = spec_from_job(job)
    key_id = key_token.split("_")[1]
    out = []
    while len(out) < count:
        res = run_walk(spec, key_id, secrets.token_hex(8))
        if res is not None:
            out.append(share_json(res))
    return out


def linear_sweep_shares(job: dict, count: int) -> list[dict]:
    """The cheater from the design doc: valid DPs with known (a, b), found by
    stepping R, R+G, R+2G, ... instead of walking f. Passes every cheap check."""
    spec = spec_from_job(job)
    curve = spec.curve
    out = []
    a, b = secrets.randbelow(curve.n), secrets.randbelow(curve.n)
    P = curve.mul2(a, b, spec.Q)
    while len(out) < count:
        P = curve.add(P, curve.G)
        a += 1
        if spec.is_dp(P):
            # Fabricate checkpoints for a plausible length.
            length = spec.checkpoint_interval * 2 + 1
            cps = []
            Pc = P
            while len(cps) < spec.n_checkpoints(length):
                Pc = curve.add(Pc, curve.G)
                if not spec.is_dp(Pc):
                    cps.append(Pc)
            out.append(share_json(WalkResult(secrets.token_hex(8), length, P, a % curve.n,
                                             b % curve.n, cps)))
    return out


def submit(client, headers, job_id, shares):
    return client.post("/v1/shares", headers=headers, json={"job_id": job_id, "shares": shares})


def drain_audits(client):
    pool = client.app.state.pool
    while pool.run_pending_audits(1000):
        pass


# ---------------------------------------------------------------- auth/keys
def test_requires_valid_key(make_app):
    c = make_app()
    assert c.get("/v1/jobs").status_code == 401
    assert c.get("/v1/jobs", headers={"Authorization": "Bearer eccp_nope_nope"}).status_code == 401
    assert c.get("/v1/jobs", headers={"Authorization": "Bearer garbage"}).status_code == 401
    _, h = issue_key(c)
    assert c.get("/v1/jobs", headers=h).status_code == 200


def test_admin_requires_token(make_app):
    c = make_app()
    assert c.post("/admin/keys", json={"label": "x"}).status_code == 401
    assert c.post("/admin/keys", json={"label": "x"},
                  headers={"Authorization": "Bearer wrong"}).status_code == 401
    token, h = issue_key(c)
    # A worker key is not an admin token.
    assert c.get("/admin/keys", headers=h).status_code == 401


def test_secret_is_not_stored(make_app):
    c = make_app()
    token, _ = issue_key(c)
    secret = token.split("_")[2]
    rows = c.app.state.db.query("SELECT * FROM api_keys")
    assert all(secret not in str(tuple(r)) for r in rows)


def test_revoke_and_suspend(make_app):
    c = make_app()
    token, h = issue_key(c)
    key_id = token.split("_")[1]
    r = c.post(f"/admin/keys/{key_id}/suspend", headers=admin_headers(), json={"reason": "look"})
    assert r.json()["status"] == "suspended"
    r = c.get("/v1/me", headers=h)
    assert r.status_code == 403 and "suspended" in r.json()["detail"]
    c.post(f"/admin/keys/{key_id}/reinstate", headers=admin_headers(), json={"reason": "ok"})
    assert c.get("/v1/me", headers=h).status_code == 200
    c.post(f"/admin/keys/{key_id}/revoke", headers=admin_headers(), json={"reason": "abuse"})
    assert c.get("/v1/me", headers=h).status_code == 403
    r = c.post(f"/admin/keys/{key_id}/reinstate", headers=admin_headers(), json={"reason": "no"})
    assert r.status_code == 409


# -------------------------------------------------------------- rate limits
def test_share_rate_limit(make_app, curve, secret_k):
    c = make_app(probation_shares=0, audit_rate=0)
    token, h = issue_key(c, shares_per_minute=1, share_burst=3)
    job = create_job(c, curve, secret_k)
    shares = honest_shares(job, token, 4)
    assert submit(c, h, job["job_id"], shares[:3]).status_code == 200
    assert submit(c, h, job["job_id"], shares[:4]).status_code == 413   # batch > burst
    r = submit(c, h, job["job_id"], shares[3:])
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_request_rate_limit(make_app):
    c = make_app()
    _, h = issue_key(c, requests_per_minute=1)
    codes = [c.get("/v1/me", headers=h).status_code for _ in range(15)]
    assert codes[0] == 200 and 429 in codes


def test_auth_failure_rate_limit(make_app):
    c = make_app()
    codes = [c.get("/v1/me", headers={"Authorization": f"Bearer eccp_{i:04d}_x"}).status_code
             for i in range(30)]
    assert codes[0] == 401 and codes[-1] == 429


def test_body_size_limit(make_app):
    c = make_app()
    _, h = issue_key(c)
    r = c.post("/v1/shares", headers=h, content=b"{" + b" " * (17 * 1024 * 1024) + b"}",
               )
    assert r.status_code == 413


# --------------------------------------------------------- cheap validation
def test_bad_representation_is_rejected_with_strike(make_app, curve, secret_k):
    c = make_app(max_strikes=3)
    token, h = issue_key(c)
    job = create_job(c, curve, secret_k)
    share = honest_shares(job, token, 1)[0]
    share["a"] = hex((int(share["a"], 16) + 1) % curve.n)
    r = submit(c, h, job["job_id"], [share])
    assert r.json()["results"][0]["code"] == "bad_representation"
    assert c.get("/v1/me", headers=h).json()["strikes"] == 1


def test_strikes_suspend_key(make_app, curve, secret_k):
    c = make_app(max_strikes=3)
    token, h = issue_key(c)
    job = create_job(c, curve, secret_k)
    bad = {"nonce": "x", "length": 1, "dp": ["0x1", "0x1"], "a": "0x1", "b": "0x1",
           "checkpoints": []}
    r = submit(c, h, job["job_id"], [dict(bad, nonce=f"n{i}") for i in range(5)])
    results = r.json()["results"]
    assert len(results) == 3          # batch stops once the key is suspended
    assert c.get("/v1/me", headers=h).status_code == 403


def test_not_distinguished_and_bad_checkpoints(make_app, curve, secret_k):
    c = make_app()
    token, h = issue_key(c)
    job = create_job(c, curve, secret_k)
    spec = spec_from_job(job)
    P = curve.mul(7, curve.G)
    while spec.is_dp(P):
        P = curve.add(P, curve.G)
    a = 7
    share = {"nonce": "aa", "length": 1, "dp": [hex(P[0]), hex(P[1])], "a": hex(a), "b": "0x0",
             "checkpoints": []}
    assert submit(c, h, job["job_id"], [share]).json()["results"][0]["code"] == "not_distinguished"
    good = honest_shares(job, token, 1)[0]
    good["length"] += spec.checkpoint_interval
    assert submit(c, h, job["job_id"], [good]).json()["results"][0]["code"] == "bad_checkpoints"


def test_duplicate_and_copied_shares(make_app, curve, secret_k):
    c = make_app(probation_shares=0, audit_rate=0)
    t1, h1 = issue_key(c, "honest")
    t2, h2 = issue_key(c, "thief")
    job = create_job(c, curve, secret_k)
    share = honest_shares(job, t1, 1)[0]
    assert submit(c, h1, job["job_id"], [share]).json()["results"][0]["status"] == "accepted"
    assert submit(c, h1, job["job_id"], [share]).json()["results"][0]["status"] == "duplicate"
    # Another key replaying the same DP with the same representation.
    stolen = dict(share, nonce="stolen1")
    r = submit(c, h2, job["job_id"], [stolen]).json()["results"][0]
    assert r["code"] == "copied_dp"


# ---------------------------------------------------------------- audits
def test_honest_worker_passes_audits(make_app, curve, secret_k):
    c = make_app(probation_shares=1000, probation_audit_rate=1.0, full_audit_rate=0.3)
    token, h = issue_key(c)
    job = create_job(c, curve, secret_k, dp_bits=8)
    r = submit(c, h, job["job_id"], honest_shares(job, token, 20))
    assert all(x["status"] == "accepted" for x in r.json()["results"])
    drain_audits(c)
    me = c.get("/v1/me", headers=h).json()
    assert me["status"] == "active"
    assert me["audits_passed"] == 20 and me["audits_failed"] == 0
    assert me["credit"]["verified"] == 20 * 256


def test_linear_sweep_cheater_is_revoked_and_forfeits(make_app, curve, secret_k):
    c = make_app(probation_shares=1000, probation_audit_rate=0.25, full_audit_rate=0)
    token, h = issue_key(c, "cheater")
    job = create_job(c, curve, secret_k, dp_bits=8)
    # Earn some honest credit first, then switch to fabricated shares.
    submit(c, h, job["job_id"], honest_shares(job, token, 5))
    r = submit(c, h, job["job_id"], linear_sweep_shares(job, 40))
    # Every fabricated share passes the cheap checks...
    assert all(x["status"] == "accepted" for x in r.json()["results"])
    # ...and the first audit that lands on one revokes the key.
    drain_audits(c)
    key_id = token.split("_")[1]
    stats = c.get(f"/admin/keys/{key_id}", headers=admin_headers()).json()
    assert stats["status"] == "revoked"
    assert stats["credit"]["earned"] == 0
    assert stats["credit"]["forfeited"] == 45 * 256
    assert c.get("/v1/me", headers=h).status_code == 403


def test_tampered_checkpoint_fails_segment_audit(make_app, curve, secret_k):
    c = make_app(probation_shares=0, audit_rate=0, full_audit_rate=0)
    token, h = issue_key(c)
    key_id = token.split("_")[1]
    job = create_job(c, curve, secret_k, dp_bits=8)
    spec = spec_from_job(job)
    while True:
        share = honest_shares(job, token, 1)[0]
        if share["checkpoints"]:
            break
    P = curve.add((int(share["checkpoints"][0][0], 16), int(share["checkpoints"][0][1], 16)),
                  curve.G)
    while spec.is_dp(P):
        P = curve.add(P, curve.G)
    share["checkpoints"][0] = [hex(P[0]), hex(P[1])]
    assert submit(c, h, job["job_id"], [share]).json()["results"][0]["status"] == "accepted"
    c.post(f"/admin/keys/{key_id}/audit", headers=admin_headers(), json={"count": 5, "kind": "full"})
    drain_audits(c)
    assert c.get(f"/admin/keys/{key_id}", headers=admin_headers()).json()["status"] == "revoked"


# ------------------------------------------------------------- end to end
def test_distributed_solve(make_app, curve, secret_k):
    c = make_app(probation_shares=10, probation_audit_rate=0.5, audit_rate=0.05)
    workers = [issue_key(c, f"w{i}", shares_per_minute=1e6, share_burst=10_000,
                         requests_per_minute=1e6) for i in range(3)]
    job = create_job(c, curve, secret_k, dp_bits=6)
    spec = spec_from_job(job)
    solved = False
    rounds = 0
    while not solved:
        rounds += 1
        assert rounds < 400, "rho should finish long before this"
        for token, h in workers:
            r = submit(c, h, job["job_id"], honest_shares(job, token, 16))
            assert r.status_code == 200, r.text
            for x in r.json()["results"]:
                assert x["status"] in ("accepted", "rejected"), x
                if x["status"] == "rejected":
                    assert x["code"] == "job_solved", x
            if any(x.get("solved") for x in r.json()["results"]):
                solved = True
                break
    j = c.get(f"/v1/jobs/{job['job_id']}", headers=workers[0][1]).json()
    assert j["status"] == "solved"
    assert int(j["solution"], 16) == secret_k
    assert curve.mul(int(j["solution"], 16), curve.G) == spec.Q
    drain_audits(c)
    for token, h in workers:
        me = c.get("/v1/me", headers=h).json()
        assert me["status"] == "active" and me["audits_failed"] == 0
    # Submitting to a solved job is refused without a strike.
    r = submit(c, workers[0][1], job["job_id"], honest_shares(job, workers[0][0], 1))
    assert r.json()["results"][0]["code"] == "job_solved"
