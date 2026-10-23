"""Resumable v2 contributor for all published Certicom instances."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ecc_challenges.multicurve.codec import (
    MAX_BATCH_BYTES, MAX_TRAIL_STEPS, WALK_IDS, WALK_V2, canonical, namespace,
)
from .runtime import BACKENDS, EngineSession, NativeState, pack_state, run_round, unpack_states
from .heartbeat import Heartbeat

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("unexpected HTTP redirect; preserve local contributor state")


OPENER = build_opener(NoRedirect)


class GatewayError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"gateway HTTP {status}; local state retained")

    @property
    def retryable(self):
        return self.status in (408, 410, 425, 429) or self.status >= 500


def atomic(path: Path, value: dict):
    fd, temporary = tempfile.mkstemp(prefix=".generic-", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        # Windows cannot open a directory to flush it; NTFS journals the
        # rename that os.replace performs.
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def lock_state_directory(root: Path):
    """Hold the state directory exclusively; a second worker fails at once."""
    handle = (root / ".contributor.lock").open("a+")
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    return handle


class API:
    def __init__(self, origin: str, key: str):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("generic gateway must be an HTTPS origin")
        self.origin, self.key = origin.rstrip("/"), key

    def request(self, method: str, path: str, body=None):
        data = canonical(body) if body is not None else (b"" if method == "POST" else None)
        request = Request(self.origin + path, method=method, data=data, headers={
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json", "User-Agent": "ecc-challenges-worker/2"})
        try:
            with OPENER.open(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never include signed URLs, response headers, or credentials in logs.
            raise GatewayError(exc.code) from None

    @staticmethod
    def upload(capability: dict, data: bytes):
        parsed = urlsplit(capability["url"])
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or not parsed.hostname or not parsed.hostname.endswith(".amazonaws.com")
                or capability["method"] != "PUT"):
            raise RuntimeError("gateway returned an unsupported upload capability")
        request = Request(capability["url"], method="PUT", data=data, headers=capability["headers"])
        try:
            with OPENER.open(request, timeout=120) as response:
                if response.status != 200:
                    raise RuntimeError("object upload was not acknowledged")
        except HTTPError as exc:
            if exc.code != 412:
                raise RuntimeError(f"object upload HTTP {exc.code}; local state retained") from None
            # Existing immutable object: authenticated commit checks exact checksum.


def flush(api: API, ns, state: dict, path: Path):
    pending = bytes.fromhex(state["pending"])
    capacity = MAX_BATCH_BYTES // ns.record_bytes * ns.record_bytes
    while pending:
        data = pending[:capacity]
        offset = state["cursor"]
        run = state["run_id"]
        intent = api.request("POST", f"/v2/runs/{run}/batches", {
            "offset": offset, "byte_length": len(data), "records": len(data) // ns.record_bytes,
            "sha256": hashlib.sha256(data).hexdigest()})
        if (intent["run_id"] != run or intent["namespace_id"] != ns.id
                or intent["offset"] != offset or intent["sha256"] != hashlib.sha256(data).hexdigest()
                or intent["byte_length"] != len(data) or intent["records"] != len(data) // ns.record_bytes):
            raise RuntimeError("upload intent binding differs from local batch")
        if intent["upload"] is not None:
            api.upload(intent["upload"], data)
        result = api.request("POST", f"/v2/runs/{run}/batches/{intent['batch_id']}/commit")
        if (result["namespace_id"] != ns.id or result["run_id"] != run
                or result["batch_id"] != intent["batch_id"]
                or result["batch_end_cursor"] != offset + len(data)
                or result["cursor"] < result["batch_end_cursor"] or not result.get("admission_key")):
            raise RuntimeError("gateway acknowledgement does not match local batch")
        pending = pending[len(data):]
        state.update(cursor=offset + len(data), pending=pending.hex())
        atomic(path, state)
        print(json.dumps({"campaign": ns.campaign, "run_id": run,
                          "uploaded_records": len(data) // ns.record_bytes,
                          "cursor": state["cursor"]}), flush=True)


def cpu_threads() -> int:
    """Threads the OpenMP engine will use: OMP_NUM_THREADS, else the CPU affinity set."""
    value = os.environ.get("OMP_NUM_THREADS", "").split(",")[0]
    if value.isdecimal() and int(value) > 0:
        return int(value)
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1


def default_lanes(backend: str, batched: bool = False) -> int:
    if batched:
        # The specialised engine shares one field inversion between the lanes
        # of a thread (up to 64) or, on a GPU, of a four-lane group, so more
        # lanes are cheaper per step and a GPU needs thousands to fill.
        return 4096 if backend in ("cuda", "metal") else min(4096, 32 * cpu_threads())
    # Four lanes per thread keeps every core busy while lanes pause at DPs.
    return 64 if backend in ("cuda", "metal") else min(4096, 4 * cpu_threads())


def engine_for_backend(backend: str, cpu_engine: Path = Path("/opt/ecc/generic-engine-cpu")) -> Path:
    override = os.environ.get("ECC_GENERIC_ENGINE")
    if override:
        return Path(override)
    if backend == "cpu" and cpu_engine.is_file():
        return cpu_engine
    return Path("/opt/ecc/generic-engine")


def probe_engine(engine: Path) -> dict:
    """The engine's --probe report, or {} for an engine that predates it."""
    try:
        result = subprocess.run([str(engine), "--probe"], capture_output=True,
                                text=True, timeout=60, check=False)
        report = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return report if isinstance(report, dict) else {}


def detect_backend() -> str:
    """Choose an available GPU backend supported by the installed engine."""
    if sys.platform == "darwin":
        report = probe_engine(engine_for_backend("metal"))
        if "metal" in report.get("backends", ()) and report.get("metal_devices"):
            return "metal"
        return "cpu"
    try:
        probe = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                               capture_output=True, timeout=15, check=False)
        if probe.returncode == 0 and probe.stdout.strip():
            report = probe_engine(engine_for_backend("cuda"))
            if "cuda" in report.get("backends", ()):
                return "cuda"
        return "cpu"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "cpu"


def select_backend() -> str:
    """Apply the worker's backend and provider-visible device selection."""
    backend = os.environ.get("ECC_BACKEND", "auto")
    if backend == "auto":
        backend = detect_backend()
    if backend == "cuda" and "ECC_GPU" in os.environ:
        selected = os.environ["ECC_GPU"]
        if "CUDA_VISIBLE_DEVICES" in os.environ:
            visible = os.environ["CUDA_VISIBLE_DEVICES"].split(",")
            if selected.isdecimal() and int(selected) < len(visible):
                selected = visible[int(selected)]
            elif selected not in visible:
                raise ValueError("ECC_GPU is outside the provider's visible GPU allocation")
        os.environ["CUDA_VISIBLE_DEVICES"] = selected
    if backend not in BACKENDS:
        raise ValueError("ECC_BACKEND must be auto, cpu, cuda or metal")
    return backend


def namespace_for_state(campaign: str, namespace_id: str):
    """The namespace a saved run was registered under, whatever the walk.

    Runs stay pinned to the walk they registered with: a state directory from
    before a campaign's default walk changed keeps running its old walk.
    """
    for version in WALK_IDS:
        try:
            candidate = namespace(campaign, None, version)
        except ValueError:  # walk not defined for this curve
            continue
        if candidate.id == namespace_id:
            return candidate
    return None


def main(root: Path, key: str, campaign: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    lock = lock_state_directory(root)  # held until the process exits
    ns = namespace(campaign)
    path = root / "generic-state.json"
    pinned = False
    if path.exists():
        saved_id = json.loads(path.read_text()).get("namespace_id")
        older = namespace_for_state(campaign, saved_id) if saved_id != ns.id else None
        if older is not None:
            ns, pinned = older, True
    api = API(os.environ.get("ECC_API_URL", "https://ecc-challenges.com"), key)
    if not pinned:
        remote_config = api.request("GET", f"/v2/config?campaign={ns.campaign}")
        if canonical(remote_config) != canonical(ns.public()):
            raise RuntimeError("worker and gateway namespaces differ; update the pinned image")
    action = os.environ.get("ECC_ACTION", "work")
    if action == "close":
        saved = json.loads((root / "generic-state.json").read_text())
        if saved["namespace_id"] != ns.id or saved["protocol"] != 2:
            raise RuntimeError("state directory belongs to another namespace")
        if saved["pending"]:
            raise RuntimeError("resume the contributor to upload its pending records before closing")
        result = api.request("POST", f"/v2/runs/{saved['run_id']}/close")
        print(json.dumps(result), flush=True)
        return 0
    if action != "work":
        raise ValueError("ECC_ACTION must be work or close")
    backend = select_backend()
    saved = json.loads(path.read_text()) if path.exists() else {}
    # Walks 3 and 4 run on the specialised engine; the published walk keeps its
    # original sizes so existing state directories resume unchanged.
    fast = ns.walk_version != WALK_V2
    lanes = int(os.environ.get("ECC_LANES") or saved.get("lanes") or default_lanes(backend, fast))
    steps = int(os.environ.get("ECC_STEPS_PER_ROUND") or saved.get("round_steps")
                or (4096 if ns.walk_version != WALK_V2 else 256))
    if not 1 <= lanes <= 4096 or not 1 <= steps <= 1048576:
        raise ValueError("invalid lane or round size")
    if (root / "state.json").exists():
        raise RuntimeError("state directory contains another protocol; select a separate volume")
    if path.exists():
        state = json.loads(path.read_text())
        if (state["namespace_id"] != ns.id or state["lanes"] != lanes or state["protocol"] != 2
                or state["round_steps"] != steps):
            raise RuntimeError("state directory has another namespace or lane count")
        status = api.request("GET", f"/v2/runs/{state['run_id']}")
        if pinned and canonical(status.get("config")) != canonical(ns.public()):
            raise RuntimeError("worker and gateway namespaces differ; update the pinned image")
        if status.get("closed_at"):
            raise RuntimeError("run is closed; preserve this checkpoint and choose a new state directory")
    else:
        nonce_path = root / "generic-registration.json"
        if nonce_path.exists():
            nonce = json.loads(nonce_path.read_text())["registration_id"]
        else:
            nonce = str(uuid.uuid4())
            atomic(nonce_path, {"registration_id": nonce, "namespace_id": ns.id})
        nonce_record = json.loads(nonce_path.read_text())
        if nonce_record["namespace_id"] != ns.id:
            raise RuntimeError("registration belongs to another namespace")
        assigned = api.request("POST", "/v2/register", {"campaign": ns.campaign, "registration_id": nonce})
        if assigned["namespace_id"] != ns.id or assigned["cursor"] != 0:
            raise RuntimeError("existing registration lacks local checkpoint; preserve it for recovery")
        state = {"protocol": 2, "namespace_id": ns.id, "run_id": assigned["run_id"],
                 "cursor": 0, "pending": "", "lanes": lanes, "next_trail": lanes,
                 "round_steps": steps,
                 "states": base64.b64encode(b"".join(pack_state(ns.start(assigned["run_id"], i))
                                                     for i in range(lanes))).decode()}
        atomic(path, state)
    stop = False

    def terminate(_signo, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    if hasattr(signal, "SIGBREAK"):  # Windows Ctrl-Break and console close
        signal.signal(signal.SIGBREAK, terminate)
    engine = engine_for_backend(backend)
    retry = 1
    # One engine process serves every round: the job is validated once, and
    # a round costs a request instead of another process start.
    with tempfile.TemporaryDirectory(prefix="ecc-round-", dir=root) as temporary, \
            Heartbeat(api.origin, key), \
            EngineSession(engine, backend, ns, lanes, temporary) as session:
        while not stop:
            try:
                flush(api, ns, state, path)
                retry = 1
            except Exception as exc:
                if isinstance(exc, GatewayError) and not exc.retryable:
                    raise
                print(json.dumps({"campaign": campaign, "event": "upload-paused",
                                  "error": type(exc).__name__, "status": getattr(exc, "status", None),
                                  "retry_seconds": retry}), flush=True)
                for _ in range(retry):
                    if stop:
                        break
                    time.sleep(1)
                retry = min(60, retry * 2)
                continue
            if stop:
                break
            states = unpack_states(base64.b64decode(state["states"], validate=True))
            result = run_round(engine, backend, ns, states, steps, Path(temporary), timeout=600,
                               session=session)
            pending = bytearray()
            next_states = []
            for item in result.states:
                if not item.active:
                    ns.verify(item.record)
                    pending.extend(ns.encode(item.record))
                if not item.active or item.steps >= min(MAX_TRAIL_STEPS, 32 * (1 << ns.dp_bits)):
                    trail = state["next_trail"]
                    if trail >= 1 << 64:
                        raise RuntimeError("trail identity space exhausted")
                    state["next_trail"] += 1
                    item = NativeState(ns.start(state["run_id"], trail), 1)
                next_states.append(item)
            # One atomic commit binds emitted records and all advanced lane states.
            state["pending"] = bytes(pending).hex()
            state["states"] = base64.b64encode(b"".join(pack_state(s.record, s.active)
                                                        for s in next_states)).decode()
            atomic(path, state)
    # Local checkpoint and pending records remain durable even if shutdown sync fails.
    try:
        flush(api, ns, state, path)
    except Exception:
        return 1
    return 0
