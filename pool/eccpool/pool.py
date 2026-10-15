"""Pool logic: jobs, share verification, collisions, audits, sanctions.

Trust model (see docs/DESIGN.md):

1. Every share gets the cheap checks: points on the curve, DP really
   distinguished, checkpoints not distinguished, and DP == a*G + b*Q.
   A share that passes can never poison the DP table: any collision
   between two such shares with different (a, b) yields log_G(Q).
2. Usefulness (the DP was reached by walking f from the key-bound start)
   is checked by random audits: replay one random checkpoint segment, or
   occasionally the whole walk. The audit is chosen by the server after
   the share is committed, so a worker cannot predict which segment.
3. A failed audit revokes the key and forfeits all of its credit. Shares
   that fail the cheap checks earn strikes; too many strikes suspend the key.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass, field

from .db import DB
from .ec import Curve
from .keys import KeyStore
from .walk import WalkSpec, replay_segment

VERIFICATION_MODES = ("audit", "stark", "stark-challenge")

log = logging.getLogger("eccpool")


@dataclass
class PoolConfig:
    audit_rate: float = 0.05
    full_audit_rate: float = 0.01
    probation_shares: int = 50
    probation_audit_rate: float = 0.5
    max_strikes: int = 10
    max_batch: int = 64
    proof_deadline_s: float = 3600.0
    max_proof_bytes: int = 512 * 1024
    store_proofs: bool = True       # keep stark-mode proofs for third-party re-verification


class ShareError(Exception):
    def __init__(self, code: str, message: str, strike: bool) -> None:
        super().__init__(message)
        self.code = code
        self.strike = strike


@dataclass
class ShareOutcome:
    nonce: str
    status: str                     # accepted | duplicate | rejected
    code: str = ""
    message: str = ""
    share_id: int | None = None
    credit: float = 0.0
    solved: bool = False
    challenge: dict | None = None

    def as_dict(self) -> dict:
        d = {"nonce": self.nonce, "status": self.status}
        if self.code:
            d["code"] = self.code
        if self.message:
            d["message"] = self.message
        if self.share_id is not None:
            d["share_id"] = self.share_id
            d["credit"] = self.credit
        if self.solved:
            d["solved"] = True
        if self.challenge:
            d["challenge"] = self.challenge
        return d


def _hex_int(v, name: str, bound: int) -> int:
    if not isinstance(v, str) or not v.startswith("0x") or len(v) > 2 + (bound.bit_length() + 3) // 4:
        raise ShareError("bad_format", f"{name} must be a 0x-prefixed hex string below the modulus", True)
    try:
        x = int(v, 16)
    except ValueError:
        raise ShareError("bad_format", f"{name} is not hex", True) from None
    if not 0 <= x < bound:
        raise ShareError("bad_format", f"{name} out of range", True)
    return x


def _point(v, name: str, p: int) -> tuple[int, int]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ShareError("bad_format", f"{name} must be [x, y]", True)
    return (_hex_int(v[0], f"{name}.x", p), _hex_int(v[1], f"{name}.y", p))


@dataclass
class Job:
    job_id: str
    name: str
    status: str
    spec: WalkSpec
    raw_spec: dict
    expected_work: float = field(init=False)

    def __post_init__(self) -> None:
        self.expected_work = math.sqrt(math.pi * self.spec.curve.n / 2)

    @property
    def verification(self) -> str:
        return self.raw_spec.get("verification", "audit")

    @property
    def challenge_rate(self) -> float:
        return float(self.raw_spec.get("challenge_rate", 0.0))


class Pool:
    def __init__(self, db: DB, keys: KeyStore, config: PoolConfig) -> None:
        self.db = db
        self.keys = keys
        self.cfg = config
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()

    # ---------------------------------------------------------------- jobs
    def create_job(self, name: str, curve: Curve, Q: tuple[int, int], dp_bits: int,
                   r: int = 32, checkpoint_interval: int = 0, max_walk_len: int = 0,
                   salt: str | None = None, verification: str = "audit",
                   challenge_rate: float = 0.25) -> str:
        if verification not in VERIFICATION_MODES:
            raise ValueError(f"verification must be one of {VERIFICATION_MODES}")
        if not 0.0 <= challenge_rate <= 1.0:
            raise ValueError("challenge_rate must be in [0, 1]")
        curve.validate()
        if not curve.in_subgroup(Q):
            raise ValueError("G and Q must be points of order n on the curve")
        if (1 << (dp_bits + r.bit_length())) >= curve.p:
            raise ValueError("dp_bits too large for this field")
        salt = salt or secrets.token_hex(16)
        if verification != "audit" and not max_walk_len:
            from .stark import MAX_STEPS
            max_walk_len = min(20 << dp_bits, MAX_STEPS)
        spec = WalkSpec(curve, Q, salt, dp_bits, r, checkpoint_interval, max_walk_len)
        if verification != "audit":
            from .stark import supports
            why = supports(spec)
            if why:
                raise ValueError(why)
        raw = {
            "spec_version": "eccpool-walk-v1",
            "curve": curve.to_dict(),
            "Q": [hex(Q[0]), hex(Q[1])],
            "salt": salt,
            "dp_bits": dp_bits,
            "r": r,
            "checkpoint_interval": spec.checkpoint_interval,
            "max_walk_len": spec.max_walk_len,
            "verification": verification,
        }
        if verification == "stark-challenge":
            raw["challenge_rate"] = challenge_rate
            raw["proof_deadline_s"] = self.cfg.proof_deadline_s
        job_id = secrets.token_hex(6)
        with self.db.tx() as c:
            c.execute("INSERT INTO jobs (job_id, name, status, spec, created_at)"
                      " VALUES (?,?, 'active', ?, ?)",
                      (job_id, name, json.dumps(raw), time.time()))
            self.db.event(c, None, "job_created", {"job_id": job_id, "name": name})
        return job_id

    def job(self, job_id: str) -> Job | None:
        row = self.db.one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if row is None:
            return None
        with self._jobs_lock:
            cached = self._jobs.get(job_id)
            if cached is None:
                raw = json.loads(row["spec"])
                curve = Curve.from_dict(raw["curve"])
                Q = (int(raw["Q"][0], 16), int(raw["Q"][1], 16))
                spec = WalkSpec(curve, Q, raw["salt"], raw["dp_bits"], raw["r"],
                                raw["checkpoint_interval"], raw["max_walk_len"])
                cached = Job(job_id, row["name"], row["status"], spec, raw)
                self._jobs[job_id] = cached
            cached.status = row["status"]
            return cached

    def set_job_status(self, job_id: str, status: str) -> bool:
        if status not in ("active", "paused"):
            raise ValueError("status must be active or paused")
        with self.db.tx() as c:
            cur = c.execute("UPDATE jobs SET status = ? WHERE job_id = ? AND status != 'solved'",
                            (status, job_id))
            if cur.rowcount:
                self.db.event(c, None, f"job_{status}", {"job_id": job_id})
            return cur.rowcount > 0

    def job_public(self, job: Job) -> dict:
        row = self.db.one("SELECT * FROM jobs WHERE job_id = ?", (job.job_id,))
        stats = self.db.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(length),0) AS steps FROM shares"
            " WHERE job_id = ? AND status != 'forfeited'", (job.job_id,))
        out = {
            "job_id": job.job_id,
            "name": job.name,
            "status": row["status"],
            "spec": job.raw_spec,
            "verification": job.verification,
            "credit_per_share": float(1 << job.spec.dp_bits),
            "shares": stats["n"],
            "reported_steps": stats["steps"],
            "expected_total_steps": job.expected_work,
        }
        if row["status"] == "solved":
            out["solution"] = row["solution"]
            out["solved_at"] = row["solved_at"]
        return out

    # -------------------------------------------------------------- shares
    def submit(self, key_id: str, job_id: str, shares: list) -> list[ShareOutcome]:
        job = self.job(job_id)
        if job is None:
            raise LookupError("unknown job")
        outcomes = []
        for raw in shares:
            nonce = raw.get("nonce") if isinstance(raw, dict) else None
            nonce = nonce if isinstance(nonce, str) else ""
            try:
                outcomes.append(self._submit_one(key_id, job, raw))
            except ShareError as e:
                outcomes.append(ShareOutcome(nonce, "rejected", e.code, str(e)))
                if e.strike:
                    self._strike(key_id, e.code, str(e))
            if self.keys.get(key_id).status != "active":
                break
        return outcomes

    def _check(self, job: Job, raw, key_id: str) -> dict:
        """The per-share checks (and, in stark mode, the proof). Raises ShareError."""
        spec, curve = job.spec, job.spec.curve
        if not isinstance(raw, dict):
            raise ShareError("bad_format", "share must be an object", True)
        nonce = raw.get("nonce")
        if not isinstance(nonce, str) or not 1 <= len(nonce) <= 64 or not (nonce.isascii() and nonce.isalnum()):
            raise ShareError("bad_format", "nonce must be 1-64 alphanumeric characters", True)
        length = raw.get("length")
        if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= spec.max_walk_len:
            raise ShareError("bad_format", f"length must be in [1, {spec.max_walk_len}]", True)
        dp = _point(raw.get("dp"), "dp", curve.p)
        a = _hex_int(raw.get("a"), "a", curve.n)
        b = _hex_int(raw.get("b"), "b", curve.n)
        cps_raw = raw.get("checkpoints", [] if job.verification == "stark" else None)
        if not isinstance(cps_raw, list):
            raise ShareError("bad_format", "checkpoints must be a list", True)
        proof = raw.get("proof")
        if job.verification == "stark":
            if cps_raw:
                raise ShareError("bad_checkpoints", "stark jobs take a proof, not checkpoints", True)
            if not isinstance(proof, str) or not 0 < len(proof) <= self.cfg.max_proof_bytes:
                raise ShareError("missing_proof", "stark jobs need a base64 proof of the walk", True)
        elif proof is not None:
            raise ShareError("bad_format", "this job does not take proofs with shares", True)
        elif len(cps_raw) != spec.n_checkpoints(length):
            raise ShareError("bad_checkpoints",
                             f"expected {spec.n_checkpoints(length)} checkpoints for length {length}", True)
        cps = [_point(c, f"checkpoints[{i}]", curve.p) for i, c in enumerate(cps_raw)]
        if not curve.on_curve(dp):
            raise ShareError("not_on_curve", "dp is not on the curve", True)
        if not spec.is_dp(dp):
            raise ShareError("not_distinguished", "dp is not a distinguished point", True)
        for i, cp in enumerate(cps):
            if not curve.on_curve(cp):
                raise ShareError("not_on_curve", f"checkpoint {i} is not on the curve", True)
            if spec.is_dp(cp):
                raise ShareError("bad_checkpoints", f"checkpoint {i} is distinguished; the walk "
                                 "must stop at its first DP", True)
        if curve.mul2(a, b, spec.Q) != dp:
            raise ShareError("bad_representation", "dp != a*G + b*Q", True)
        if spec.start(key_id, nonce)[2] is None:
            raise ShareError("bad_nonce", "this nonce's start point is the identity; use another",
                             False)
        if job.verification == "stark":
            from .stark import engine
            _, _, start = spec.start(key_id, nonce)
            err = engine().verify(spec, start, dp, length, True, proof)
            if err:
                raise ShareError("bad_proof", f"STARK proof rejected: {err}", True)
        return {"nonce": nonce, "length": length, "dp": dp, "a": a, "b": b, "cps": cps,
                "proof": proof}

    def _submit_one(self, key_id: str, job: Job, raw) -> ShareOutcome:
        s = self._check(job, raw, key_id)   # expensive part runs outside the write lock
        n = job.spec.curve.n
        credit = float(1 << job.spec.dp_bits)
        dp_x, dp_y = hex(s["dp"][0]), hex(s["dp"][1])
        with self.db.tx() as c:
            jrow = c.execute("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            if jrow["status"] != "active":
                raise ShareError("job_" + jrow["status"], f"job is {jrow['status']}", False)
            krow = c.execute("SELECT status, accepted_shares FROM api_keys WHERE key_id = ?",
                             (key_id,)).fetchone()
            if krow["status"] != "active":
                raise ShareError("key_inactive", f"key is {krow['status']}", False)
            dup = c.execute("SELECT share_id, dp_x, dp_y FROM shares WHERE job_id = ? AND key_id = ?"
                            " AND nonce = ?", (job.job_id, key_id, s["nonce"])).fetchone()
            if dup is not None:
                if (dup["dp_x"], dup["dp_y"]) == (dp_x, dp_y):
                    return ShareOutcome(s["nonce"], "duplicate", "duplicate",
                                        "share already accepted", dup["share_id"], 0.0)
                raise ShareError("nonce_reused", "nonce already used with a different result", True)
            prior = c.execute("SELECT * FROM dps WHERE job_id = ? AND dp_x = ? AND dp_y = ?",
                              (job.job_id, dp_x, dp_y)).fetchone()
            if prior is not None and int(prior["a"], 16) == s["a"] and int(prior["b"], 16) == s["b"]:
                # Same point, same representation: a copy of an existing DP.
                raise ShareError("copied_dp", "this distinguished point and representation were "
                                 "already submitted", True)
            cur = c.execute(
                """INSERT INTO shares (job_id, key_id, nonce, length, dp_x, dp_y, a, b,
                   checkpoints, proof, credit, status, submitted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.job_id, key_id, s["nonce"], s["length"], dp_x, dp_y, hex(s["a"]), hex(s["b"]),
                 json.dumps([[hex(x), hex(y)] for x, y in s["cps"]]),
                 s["proof"] if self.cfg.store_proofs else None, credit,
                 "verified" if job.verification == "stark" else "accepted", time.time()))
            share_id = cur.lastrowid
            c.execute("UPDATE api_keys SET accepted_shares = accepted_shares + 1, last_used_at = ?"
                      " WHERE key_id = ?", (time.time(), key_id))
            solved = False
            if prior is None:
                c.execute("INSERT INTO dps (job_id, dp_x, dp_y, a, b, share_id, key_id)"
                          " VALUES (?,?,?,?,?,?,?)",
                          (job.job_id, dp_x, dp_y, hex(s["a"]), hex(s["b"]), share_id, key_id))
            else:
                solved = self._collide(c, job, prior, share_id, s["a"], s["b"])
            challenge = self._schedule_audits(c, key_id, share_id, s["length"], job,
                                              krow["accepted_shares"])
        return ShareOutcome(s["nonce"], "accepted", share_id=share_id, credit=credit, solved=solved,
                            challenge=challenge)

    def _collide(self, c, job: Job, prior, share_id: int, a2: int, b2: int) -> bool:
        n = job.spec.curve.n
        a1, b1 = int(prior["a"], 16), int(prior["b"], 16)
        k = None
        if (b2 - b1) % n:
            k = (a1 - a2) * pow(b2 - b1, -1, n) % n
            if job.spec.curve.mul(k, job.spec.curve.G) != job.spec.Q:
                k = None
        c.execute("INSERT INTO collisions (job_id, share_a, share_b, useful, created_at)"
                  " VALUES (?,?,?,?,?)", (job.job_id, prior["share_id"], share_id,
                                          int(k is not None), time.time()))
        if k is None:
            self.db.event(c, None, "collision_useless", {"job_id": job.job_id, "share": share_id})
            return False
        c.execute("UPDATE jobs SET status = 'solved', solved_at = ?, solution = ?, solved_by_share = ?"
                  " WHERE job_id = ?", (time.time(), hex(k), share_id, job.job_id))
        self.db.event(c, None, "job_solved", {"job_id": job.job_id, "share_a": prior["share_id"],
                                              "share_b": share_id})
        log.warning("job %s solved by shares %s and %s", job.job_id, prior["share_id"], share_id)
        return True

    def _schedule_audits(self, c, key_id: str, share_id: int, length: int, job: Job,
                         accepted_before: int) -> dict | None:
        probation = accepted_before < self.cfg.probation_shares
        now = time.time()
        if job.verification == "stark":
            return None                  # the proof already covered the whole walk
        if job.verification == "stark-challenge":
            rate = max(job.challenge_rate, self.cfg.probation_audit_rate if probation else 0.0)
            if secrets.randbelow(1_000_000) >= rate * 1_000_000:
                return None
            seg = secrets.randbelow(job.spec.n_segments(length))
            lo, hi = job.spec.segment_bounds(length, seg)
            deadline = now + float(job.raw_spec.get("proof_deadline_s", self.cfg.proof_deadline_s))
            cur = c.execute("INSERT INTO audits (share_id, key_id, kind, segment, status, created_at,"
                            " deadline) VALUES (?,?, 'proof', ?, 'pending', ?, ?)",
                            (share_id, key_id, seg, now, deadline))
            c.execute("UPDATE shares SET status = 'challenged' WHERE share_id = ?", (share_id,))
            return {"challenge_id": cur.lastrowid, "segment": seg, "from_step": lo, "to_step": hi,
                    "deadline": deadline}
        rate = self.cfg.probation_audit_rate if probation else self.cfg.audit_rate
        if secrets.randbelow(1_000_000) < self.cfg.full_audit_rate * 1_000_000:
            c.execute("INSERT INTO audits (share_id, key_id, kind, status, created_at)"
                      " VALUES (?,?, 'full', 'pending', ?)", (share_id, key_id, now))
        elif secrets.randbelow(1_000_000) < rate * 1_000_000:
            seg = secrets.randbelow(job.spec.n_segments(length))
            c.execute("INSERT INTO audits (share_id, key_id, kind, segment, status, created_at)"
                      " VALUES (?,?, 'segment', ?, 'pending', ?)", (share_id, key_id, seg, now))
        return None

    def _strike(self, key_id: str, code: str, message: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE api_keys SET strikes = strikes + 1 WHERE key_id = ?", (key_id,))
            row = c.execute("SELECT strikes, status FROM api_keys WHERE key_id = ?",
                            (key_id,)).fetchone()
            self.db.event(c, key_id, "strike", {"code": code, "message": message,
                                                "strikes": row["strikes"]})
            if row["strikes"] >= self.cfg.max_strikes and row["status"] == "active":
                self.keys.set_status(key_id, "suspended",
                                     f"{row['strikes']} invalid shares (last: {code})", conn=c)

    # -------------------------------------------------------------- audits
    def audit_share(self, share_row, kind: str, segment: int | None) -> str | None:
        """Replay a segment (or the whole walk). Returns an error or None."""
        job = self.job(share_row["job_id"])
        spec = job.spec
        cps = [(int(x, 16), int(y, 16)) for x, y in json.loads(share_row["checkpoints"])]
        dp = (int(share_row["dp_x"], 16), int(share_row["dp_y"], 16))
        length = share_row["length"]
        a0, b0, start = spec.start(share_row["key_id"], share_row["nonce"])
        anchors = [start] + cps + [dp]
        if kind == "segment":
            segs = [segment]
        else:
            segs = list(range(spec.n_segments(length)))
        for seg in segs:
            lo, hi = spec.segment_bounds(length, seg)
            last = seg == spec.n_segments(length) - 1
            end, err = replay_segment(spec, anchors[seg], hi - lo, expect_dp_at_end=last)
            if err:
                return f"segment {seg}: {err}"
            if end != anchors[seg + 1]:
                return f"segment {seg}: replay from step {lo} does not reach the submitted " \
                       f"{'dp' if last else 'checkpoint'} at step {hi}"
        if kind == "full":
            # Representation is already bound by dp == a*G + b*Q; also check it
            # is the one the walk accumulates, so the share is exactly reproducible.
            n = spec.curve.n
            a, b, P = a0, b0, start
            for _ in range(length):
                c, d, R = spec.multipliers[spec.index(P)]
                P = spec.curve.add(P, R)
                a, b = (a + c) % n, (b + d) % n
            if (a, b) != (int(share_row["a"], 16), int(share_row["b"], 16)):
                return "representation does not match the walk"
        return None

    def run_pending_audits(self, limit: int = 100) -> int:
        done = 0
        while done < limit:
            row = self.db.one("SELECT * FROM audits WHERE status = 'pending' AND kind != 'proof'"
                              " ORDER BY audit_id LIMIT 1")
            if row is None:
                break
            self.run_audit(row)
            done += 1
        return done

    def run_audit(self, audit_row) -> str:
        share = self.db.one("SELECT * FROM shares WHERE share_id = ?", (audit_row["share_id"],))
        key = self.db.one("SELECT status FROM api_keys WHERE key_id = ?", (audit_row["key_id"],))
        if share["status"] == "forfeited" or key["status"] == "revoked":
            with self.db.tx() as c:
                c.execute("UPDATE audits SET status = 'skipped', finished_at = ?, detail = ?"
                          " WHERE audit_id = ?", (time.time(), "key already revoked",
                                                  audit_row["audit_id"]))
            return "skipped"
        err = self.audit_share(share, audit_row["kind"], audit_row["segment"])
        with self.db.tx() as c:
            status = "failed" if err else "passed"
            c.execute("UPDATE audits SET status = ?, finished_at = ?, detail = ? WHERE audit_id = ?",
                      (status, time.time(), err or "", audit_row["audit_id"]))
            if err:
                c.execute("UPDATE api_keys SET audits_failed = audits_failed + 1 WHERE key_id = ?",
                          (audit_row["key_id"],))
                self.forfeit(c, audit_row["key_id"],
                             f"audit {audit_row['audit_id']} failed on share {share['share_id']}: {err}")
            else:
                c.execute("UPDATE api_keys SET audits_passed = audits_passed + 1 WHERE key_id = ?",
                          (audit_row["key_id"],))
                c.execute("UPDATE shares SET status = 'verified' WHERE share_id = ?"
                          " AND status IN ('accepted', 'challenged')", (share["share_id"],))
        return status

    # ------------------------------------------------------ proof challenges
    def pending_challenges(self, key_id: str) -> list[dict]:
        rows = self.db.query(
            """SELECT a.audit_id, a.segment, a.deadline, s.share_id, s.job_id, s.nonce, s.length
               FROM audits a JOIN shares s ON s.share_id = a.share_id
               WHERE a.key_id = ? AND a.kind = 'proof' AND a.status = 'pending'
               ORDER BY a.audit_id LIMIT 500""", (key_id,))
        out = []
        for r in rows:
            lo, hi = self.job(r["job_id"]).spec.segment_bounds(r["length"], r["segment"])
            out.append({"challenge_id": r["audit_id"], "job_id": r["job_id"], "nonce": r["nonce"],
                        "share_id": r["share_id"], "segment": r["segment"], "from_step": lo,
                        "to_step": hi, "deadline": r["deadline"]})
        return out

    def answer_challenge(self, key_id: str, challenge_id: int, proof) -> dict:
        """Verify a segment proof. Returns {"status": passed|failed, ...}.

        Raises LookupError if there is no such pending challenge for this key,
        ValueError if the upload is not a proof at all (no penalty)."""
        a = self.db.one("SELECT * FROM audits WHERE audit_id = ? AND key_id = ? AND kind = 'proof'",
                        (challenge_id, key_id))
        if a is None:
            raise LookupError("no such challenge")
        if a["status"] != "pending":
            return {"challenge_id": challenge_id, "status": a["status"], "detail": a["detail"]}
        if not isinstance(proof, str) or not 0 < len(proof) <= self.cfg.max_proof_bytes:
            raise ValueError("proof must be a non-empty base64 string")
        if time.time() > a["deadline"]:
            self.expire_challenges()
            return {"challenge_id": challenge_id, "status": "failed", "detail": "deadline passed"}
        share = self.db.one("SELECT * FROM shares WHERE share_id = ?", (a["share_id"],))
        job = self.job(share["job_id"])
        spec = job.spec
        cps = [(int(x, 16), int(y, 16)) for x, y in json.loads(share["checkpoints"])]
        dp = (int(share["dp_x"], 16), int(share["dp_y"], 16))
        _, _, start = spec.start(key_id, share["nonce"])
        anchors = [start] + cps + [dp]
        seg = a["segment"]
        lo, hi = spec.segment_bounds(share["length"], seg)
        last = seg == spec.n_segments(share["length"]) - 1
        from .stark import engine
        err = engine().verify(spec, anchors[seg], anchors[seg + 1], hi - lo, last, proof)
        if err and ("base64" in err or "malformed" in err):
            raise ValueError(err)
        with self.db.tx() as c:
            row = c.execute("SELECT status FROM audits WHERE audit_id = ?", (challenge_id,)).fetchone()
            if row["status"] != "pending":
                return {"challenge_id": challenge_id, "status": row["status"]}
            status = "failed" if err else "passed"
            c.execute("UPDATE audits SET status = ?, finished_at = ?, detail = ? WHERE audit_id = ?",
                      (status, time.time(), err or "", challenge_id))
            if err:
                c.execute("UPDATE api_keys SET audits_failed = audits_failed + 1 WHERE key_id = ?",
                          (key_id,))
                self.forfeit(c, key_id, f"proof for challenge {challenge_id} (share "
                                        f"{share['share_id']}) rejected: {err}")
            else:
                c.execute("UPDATE api_keys SET audits_passed = audits_passed + 1 WHERE key_id = ?",
                          (key_id,))
                c.execute("UPDATE shares SET status = 'verified' WHERE share_id = ?"
                          " AND status = 'challenged'", (share["share_id"],))
        return {"challenge_id": challenge_id, "status": status, **({"detail": err} if err else {})}

    def expire_challenges(self, now: float | None = None) -> int:
        """An unanswered challenge counts as a failed audit."""
        now = time.time() if now is None else now
        rows = self.db.query("SELECT * FROM audits WHERE kind = 'proof' AND status = 'pending'"
                             " AND deadline < ?", (now,))
        for a in rows:
            with self.db.tx() as c:
                cur = c.execute("UPDATE audits SET status = 'failed', finished_at = ?, detail ="
                                " 'no proof before the deadline' WHERE audit_id = ? AND status ="
                                " 'pending'", (now, a["audit_id"]))
                if cur.rowcount:
                    c.execute("UPDATE api_keys SET audits_failed = audits_failed + 1"
                              " WHERE key_id = ?", (a["key_id"],))
                    self.forfeit(c, a["key_id"], f"challenge {a['audit_id']} on share "
                                                 f"{a['share_id']} expired without a proof")
        return len(rows)

    def forfeit(self, c, key_id: str, reason: str) -> None:
        """Revoke the key and forfeit all its credit. DPs stay in the table:
        they passed the representation check, so they cannot cause a false solve."""
        cur = c.execute("UPDATE shares SET status = 'forfeited' WHERE key_id = ?"
                        " AND status != 'forfeited'", (key_id,))
        self.keys.set_status(key_id, "revoked", reason, conn=c)
        self.db.event(c, key_id, "credit_forfeited", {"shares": cur.rowcount, "reason": reason})
        c.execute("UPDATE audits SET status = 'skipped', finished_at = ?, detail = 'key revoked'"
                  " WHERE key_id = ? AND status = 'pending'", (time.time(), key_id))

    def queue_audits_for_key(self, key_id: str, count: int, kind: str = "segment") -> int:
        """Admin: audit ``count`` random un-forfeited shares of a key."""
        rows = self.db.query("SELECT share_id, job_id, length FROM shares WHERE key_id = ?"
                             " AND status != 'forfeited' ORDER BY RANDOM() LIMIT ?", (key_id, count))
        with self.db.tx() as c:
            for r in rows:
                seg = None
                if kind == "segment":
                    seg = secrets.randbelow(self.job(r["job_id"]).spec.n_segments(r["length"]))
                c.execute("INSERT INTO audits (share_id, key_id, kind, segment, status, created_at)"
                          " VALUES (?,?,?,?, 'pending', ?)", (r["share_id"], key_id, kind, seg,
                                                              time.time()))
        return len(rows)

    # --------------------------------------------------------------- stats
    def key_stats(self, key_id: str) -> dict:
        k = self.db.one("SELECT * FROM api_keys WHERE key_id = ?", (key_id,))
        credit = self.db.one(
            """SELECT COALESCE(SUM(CASE WHEN status IN ('accepted','verified') THEN credit END), 0) AS earned,
                      COALESCE(SUM(CASE WHEN status = 'challenged' THEN credit END), 0) AS pending,
                      COALESCE(SUM(CASE WHEN status = 'verified' THEN credit END), 0) AS verified,
                      COALESCE(SUM(CASE WHEN status = 'forfeited' THEN credit END), 0) AS forfeited
               FROM shares WHERE key_id = ?""", (key_id,))
        return {
            "key_id": k["key_id"], "label": k["label"], "status": k["status"],
            "status_reason": k["status_reason"], "created_at": k["created_at"],
            "last_used_at": k["last_used_at"],
            "limits": {"shares_per_minute": k["shares_per_minute"], "share_burst": k["share_burst"],
                       "requests_per_minute": k["requests_per_minute"]},
            "accepted_shares": k["accepted_shares"], "strikes": k["strikes"],
            "audits_passed": k["audits_passed"], "audits_failed": k["audits_failed"],
            "credit": {"earned": credit["earned"], "pending_proof": credit["pending"],
                       "verified": credit["verified"],
                       "forfeited": credit["forfeited"]},
        }


class Auditor(threading.Thread):
    """Background thread draining the audit queue."""

    def __init__(self, pool: Pool, interval: float = 0.5) -> None:
        super().__init__(daemon=True, name="eccpool-auditor")
        self.pool = pool
        self.interval = interval
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                self.pool.expire_challenges()
                if self.pool.run_pending_audits(50) == 0:
                    self._halt.wait(self.interval)
            except Exception:  # keep auditing; one bad row must not stop the thread
                log.exception("audit loop error")
                self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()
