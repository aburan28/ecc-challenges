"""Reference worker: fetch a job, walk, submit shares.

Pure Python, so it is slow (roughly 10^5-10^6 steps/s per core); it is the
executable form of the walk spec. Fast workers (C, CUDA) must produce
byte-identical shares, which ``tests/test_walk.py`` vectors pin down.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import secrets
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import httpx

from .ec import Curve
from .walk import WalkResult, WalkSpec, run_walk  # noqa: F401

log = logging.getLogger("eccpool.worker")


def spec_from_job(job: dict) -> WalkSpec:
    raw = job["spec"]
    curve = Curve.from_dict(raw["curve"])
    Q = (int(raw["Q"][0], 16), int(raw["Q"][1], 16))
    return WalkSpec(curve, Q, raw["salt"], raw["dp_bits"], raw["r"],
                    raw["checkpoint_interval"], raw["max_walk_len"])


def share_json(res: WalkResult) -> dict:
    return {
        "nonce": res.nonce,
        "length": res.length,
        "dp": [hex(res.dp[0]), hex(res.dp[1])],
        "a": hex(res.a),
        "b": hex(res.b),
        "checkpoints": [[hex(x), hex(y)] for x, y in res.checkpoints],
    }


_SPEC: WalkSpec | None = None
_KEY_ID = ""
_MODE = "audit"
_NATIVE = None


def _init(job: dict, key_id: str, native: bool) -> None:
    global _SPEC, _KEY_ID, _MODE, _NATIVE
    _SPEC = spec_from_job(job)
    _KEY_ID = key_id
    _MODE = job["spec"].get("verification", "audit")
    if native:
        from .stark import StarkEngine
        _NATIVE = StarkEngine(procs=1)


def _walk(nonce: str) -> dict | None:
    """One walk -> one share (JSON-ready), or None if the walk was abandoned."""
    if _NATIVE is None:
        res = run_walk(_SPEC, _KEY_ID, nonce)
        return share_json(res) if res else None
    out = _NATIVE.work(_SPEC, _KEY_ID, nonce, prove=(_MODE == "stark"))
    if out is None:
        return None
    share = {"nonce": nonce, "length": out["length"], "dp": out["dp"], "a": out["a"],
             "b": out["b"], "checkpoints": [] if _MODE == "stark" else out["checkpoints"]}
    if _MODE == "stark":
        share["proof"] = out["proof"]
    return share


def answer_challenges(client: "Client", spec: WalkSpec, challenges: list[dict]) -> None:
    """Prove each challenged segment. Stateless: the walk is replayed from
    (key, nonce), so a restarted worker can still answer old challenges."""
    from .stark import engine
    eng = engine()
    for ch in challenges:
        out = eng.work(spec, client.key_id, ch["nonce"], prove=False)
        if out is None:
            log.error("cannot replay walk for challenge %s", ch["challenge_id"])
            continue
        _, _, start = spec.start(client.key_id, ch["nonce"])
        anchors = [start] + [(int(x, 16), int(y, 16)) for x, y in out["checkpoints"]]
        seg = ch["segment"]
        last = seg == spec.n_segments(out["length"]) - 1
        _, proof = eng.prove(spec, anchors[seg], ch["to_step"] - ch["from_step"], last)
        r = client._call("POST", f"/v1/challenges/{ch['challenge_id']}/proof", json={"proof": proof})
        if r.get("status") != "passed":
            log.error("challenge %s: %s", ch["challenge_id"], r)


class Client:
    def __init__(self, url: str, api_key: str, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=timeout, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}"})
        self.key_id = api_key.split("_")[1]

    def _call(self, method: str, path: str, **kw) -> dict:
        for attempt in range(8):
            r = self.http.request(method, path, **kw)
            if r.status_code == 429:
                delay = float(r.headers.get("retry-after", 2 ** attempt))
                log.info("rate limited; sleeping %.0fs", delay)
                time.sleep(delay)
                continue
            if r.status_code in (401, 403):
                raise SystemExit(f"server refused the key: {r.json().get('detail')}")
            r.raise_for_status()
            return r.json()
        raise RuntimeError("still rate limited after retries")

    def job(self, job_id: str) -> dict:
        return self._call("GET", f"/v1/jobs/{job_id}")

    def jobs(self) -> list[dict]:
        return self._call("GET", "/v1/jobs")["jobs"]

    def submit(self, job_id: str, shares: list[dict]) -> dict:
        return self._call("POST", "/v1/shares", json={"job_id": job_id, "shares": shares})

    def me(self) -> dict:
        return self._call("GET", "/v1/me")

    def challenges(self) -> list[dict]:
        return self._call("GET", "/v1/challenges")["challenges"]


def work(client: Client, job_id: str, procs: int = 1, batch: int = 16,
         max_shares: int | None = None, flush_seconds: float = 10.0) -> dict:
    """Walk and submit until the job is solved (or ``max_shares`` are sent)."""
    job = client.job(job_id)
    if job["status"] != "active":
        return job
    spec = spec_from_job(job)
    mode = job["spec"].get("verification", "audit")
    native = False
    if mode != "audit":
        from .stark import find_binary, supports
        if supports(spec) is None and find_binary():
            native = True
        else:
            raise SystemExit("this job needs STARK proofs: build stark/ "
                             "(cargo build --release) or set ECCPOOL_STARK_BIN")
    if mode == "stark-challenge":
        answer_challenges(client, spec, [c for c in client.challenges()
                                         if c["job_id"] == job_id])
    sent = 0
    pending: list[dict] = []
    last_flush = time.monotonic()

    def flush() -> bool:
        nonlocal pending, sent, last_flush
        if not pending:
            return False
        resp = client.submit(job_id, pending)
        sent += len(pending)
        pending = []
        last_flush = time.monotonic()
        for r in resp["results"]:
            if r["status"] == "rejected" and r.get("code") != "job_solved":
                log.warning("share rejected: %s", r)
        challenged = [dict(r["challenge"], nonce=r["nonce"], job_id=job_id)
                      for r in resp["results"] if r.get("challenge")]
        if challenged:
            answer_challenges(client, spec, challenged)
        if any(r.get("code") == "job_solved" or r.get("solved") for r in resp["results"]):
            return True
        return False

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(procs, mp_context=ctx, initializer=_init,
                             initargs=(job, client.key_id, native)) as ex:
        inflight = {ex.submit(_walk, secrets.token_hex(12)) for _ in range(procs * 2)}
        while True:
            done, inflight = wait(inflight, return_when=FIRST_COMPLETED)
            for f in done:
                res = f.result()
                if res is not None:
                    pending.append(res)
            stop = max_shares is not None and sent + len(pending) >= max_shares
            if len(pending) >= batch or stop or time.monotonic() - last_flush > flush_seconds:
                if flush() or stop:
                    break
                if client.job(job_id)["status"] != "active":
                    break
            for _ in done:
                inflight.add(ex.submit(_walk, secrets.token_hex(12)))
        for f in inflight:
            f.cancel()
    flush()
    return client.job(job_id)
