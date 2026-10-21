#!/usr/bin/env python3
"""Run one ECC2K-130 CUDA client and upload its records to the public gateway."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from protocol import CAMPAIGN, DP_WEIGHT, DP_WEIGHTS, WALK_ID, MAX_BATCH_BYTES, check_checkpoint
from generic.heartbeat import Heartbeat


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message, flush=True)


def read_api_key() -> str:
    """Accept a mounted secret file or a provider-injected environment secret."""
    from_environment = os.environ.pop("ECC_API_KEY", None)
    key_path = Path(os.environ.get("ECC_API_KEY_FILE", "/run/secrets/ecc_api_key"))
    if from_environment is not None and key_path.is_file():
        raise ValueError("provide the API key by file or environment, not both")
    key = (from_environment if from_environment is not None else key_path.read_text()).strip()
    if not re.fullmatch(r"ecc1_[0-9a-f]{24}_[A-Za-z0-9_-]{43}", key):
        raise ValueError("contributor API key is invalid")
    return key


def atomic_json(path: Path, obj: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(obj, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class API:
    def __init__(self, url: str, api_key: str, campaign: str = CAMPAIGN):
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and
              parsed.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("ECC_API_URL must use HTTPS outside localhost")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ECC_API_URL must not contain credentials or query parameters")
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.campaign = campaign
        self.run_id = None
        self.registration_id = None
        self.dp_weight = DP_WEIGHT

    def request(self, method: str, path: str, data: bytes | None = None):
        headers = {"User-Agent": "ecc2k130-public-client/1",
                   "X-Campaign-Id": self.campaign}
        headers["Authorization"] = "Bearer " + self.api_key
        if path == "/v1/register":
            headers["X-Registration-Id"] = self.registration_id
            headers["X-DP-Weight"] = str(self.dp_weight)
            headers["X-Walk-Id"] = WALK_ID
        if data is not None and path != "/v1/register":
            headers["Content-Type"] = "application/octet-stream"
        req = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=600 if "/checkpoint" in path else 90) as response:
                body = response.read()
                if response.headers.get("Content-Type", "").startswith("application/json"):
                    return json.loads(body)
                return body
        except HTTPError as exc:
            # Never print response headers or request headers; they can hold tokens.
            try:
                detail = json.loads(exc.read()).get("error", "request refused")
            except Exception:
                detail = "request refused"
            raise RuntimeError(f"gateway HTTP {exc.code}: {detail}") from None


def ensure_identity(api: API, root: Path) -> dict:
    state_path = root / "state.json"
    config = api.request("GET", f"/v1/config?campaign={api.campaign}")
    if (config.get("campaign"), config.get("walk_id"),
            config.get("record_bytes")) != (CAMPAIGN, WALK_ID, 32):
        raise RuntimeError("gateway campaign or record format is incompatible")
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        api.dp_weight = state.get("dp_weight", DP_WEIGHT)
        if type(api.dp_weight) is not int or api.dp_weight not in DP_WEIGHTS:
            raise RuntimeError("saved run has an unsupported sigma DP weight")
        if int(os.environ.get("ECC_DP_WEIGHT", str(api.dp_weight))) != api.dp_weight:
            raise RuntimeError("existing run has a different reporting threshold; use a separate state directory")
    else:
        if any((root / name).exists() for name in ("dps.bin", "state.ck")):
            raise RuntimeError("walk data exists without its run identity; preserve it for recovery")
        nonce_path = root / "registration.json"
        if nonce_path.is_file():
            registration = json.loads(nonce_path.read_text())
            nonce = registration["registration_id"]
            # A retry must retain the original threshold even if the server's
            # recommendation changes after accepting the first request.
            chosen = registration.get("dp_weight", int(os.environ.get("ECC_DP_WEIGHT", str(DP_WEIGHT))))
            if type(chosen) is not int or int(os.environ.get("ECC_DP_WEIGHT", str(chosen))) != chosen:
                raise RuntimeError("pending registration has a different reporting threshold; preserve its state directory")
        else:
            nonce = str(uuid.uuid4())
            chosen = int(os.environ.get("ECC_DP_WEIGHT", str(config.get("recommended_dp_weight", config.get("dp_weight", DP_WEIGHT)))))
        api.dp_weight = chosen
        if api.dp_weight not in DP_WEIGHTS or api.dp_weight not in config.get("supported_dp_weights", [config.get("dp_weight")]):
            raise RuntimeError("gateway does not support the requested sigma reporting threshold")
        atomic_json(nonce_path, {"registration_id": nonce, "dp_weight": api.dp_weight})
        api.registration_id = nonce
        result = api.request("POST", "/v1/register", b"")
        if (result.get("campaign"), result.get("walk_id"), result.get("dp_weight", DP_WEIGHT)) != (api.campaign, WALK_ID, api.dp_weight):
            raise RuntimeError("gateway assigned an incompatible run; registration identity preserved")
        state = {"protocol": "ecc-challenges-v1", "campaign": api.campaign,
                 "run_id": result["run_id"],
                 "dp_weight": api.dp_weight,
                 "remote_cursor": 0, "uploaded_local": 0}
        atomic_json(state_path, state)
    if state.get("protocol") != "ecc-challenges-v1":
        raise RuntimeError("existing state uses another gateway protocol; preserve it for migration")
    if state.get("campaign", CAMPAIGN) != api.campaign:
        raise RuntimeError("state directory belongs to another campaign; preserve it for recovery")
    api.run_id = int(state["run_id"])
    remote = api.request("GET", f"/v1/runs/{api.run_id}")
    if remote["run_id"] != api.run_id:
        raise RuntimeError("gateway returned a different run identity")
    if remote.get("campaign", CAMPAIGN) != api.campaign:
        raise RuntimeError("gateway run belongs to another campaign")
    if remote.get("dp_weight", DP_WEIGHT) != api.dp_weight or remote.get("walk_id", WALK_ID) != WALK_ID:
        raise RuntimeError("gateway run has a different sigma walk or reporting threshold")
    dp_path = root / "dps.bin"
    ck_path = root / "state.ck"
    if not dp_path.is_file():
        if remote["checkpoint_at"] is not None:
            data = api.request("GET", f"/v1/runs/{api.run_id}/checkpoint")
            check_checkpoint(data, api.run_id, api.dp_weight)
            temporary = ck_path.with_suffix(".download")
            temporary.write_bytes(data)
            os.replace(temporary, ck_path)
        elif int(remote["cursor"]):
            raise RuntimeError("local DP file was lost and gateway has no checkpoint")
        dp_path.touch(mode=0o600)
        state["remote_cursor"] = int(remote["cursor"])
        state["uploaded_local"] = 0
        atomic_json(state_path, state)
    else:
        ahead = int(remote["cursor"]) - int(state["remote_cursor"])
        if ahead < 0 or state["uploaded_local"] + ahead > dp_path.stat().st_size:
            raise RuntimeError("local DP file and gateway cursor disagree; preserve both for recovery")
        if ahead:
            state["uploaded_local"] += ahead
            state["remote_cursor"] += ahead
            atomic_json(state_path, state)
        if not ck_path.is_file() and remote["checkpoint_at"] is not None:
            data = api.request("GET", f"/v1/runs/{api.run_id}/checkpoint")
            check_checkpoint(data, api.run_id, api.dp_weight)
            temporary = ck_path.with_suffix(".download")
            temporary.write_bytes(data)
            os.replace(temporary, ck_path)
    return state


def upload_dps(api: API, root: Path, state: dict) -> int:
    path = root / "dps.bin"
    whole_size = path.stat().st_size // 32 * 32
    while state["uploaded_local"] < whole_size:
        amount = min(MAX_BATCH_BYTES, whole_size - state["uploaded_local"])
        with path.open("rb") as source:
            source.seek(state["uploaded_local"])
            data = source.read(amount)
        if len(data) != amount:
            raise RuntimeError("DP file changed while being read")
        offset = state["remote_cursor"]
        result = api.request("POST", f"/v1/runs/{api.run_id}/dps?offset={offset}", data)
        if result["cursor"] < offset + len(data):
            raise RuntimeError("gateway did not acknowledge uploaded DP bytes")
        state["uploaded_local"] += len(data)
        state["remote_cursor"] = offset + len(data)
        atomic_json(root / "state.json", state)
        log(f"run {api.run_id}: uploaded {len(data) // 32} DP records; cursor={state['remote_cursor']}")
    return whole_size


def upload_checkpoint(api: API, root: Path, state: dict, *, force: bool = False) -> None:
    path = root / "state.ck"
    if not path.is_file():
        return
    size = path.stat().st_size
    if size > 256 * 1024 * 1024:
        raise RuntimeError("checkpoint exceeds gateway limit")
    interval = max(60, int(os.environ.get("ECC_REMOTE_CHECKPOINT_SECONDS", "900")))
    if not force and time.time() - float(state.get("checkpoint_uploaded_at", 0)) < interval:
        return
    data = path.read_bytes()
    if len(data) != size:
        raise RuntimeError("checkpoint changed while being read")
    check_checkpoint(data, api.run_id, api.dp_weight)
    digest = hashlib.sha256(data).hexdigest()
    if digest == state.get("checkpoint_sha256"):
        return
    # The client flushes DPs before saving a checkpoint. Read the DP file a
    # second time to cover reports written while this checkpoint was copied.
    upload_dps(api, root, state)
    result = api.request("POST", f"/v1/runs/{api.run_id}/checkpoint?offset={state['remote_cursor']}", data)
    if result["sha256"] != digest:
        raise RuntimeError("gateway checkpoint digest differs")
    state["checkpoint_sha256"] = digest
    state["checkpoint_uploaded_at"] = time.time()
    atomic_json(root / "state.json", state)
    log(f"run {api.run_id}: checkpoint saved at DP cursor {state['remote_cursor']}")


def main() -> int:
    root = Path(os.environ.get("ECC_STATE_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    key = read_api_key()
    campaign = os.environ.get("ECC_CAMPAIGN", CAMPAIGN).lower()
    protocol = os.environ.get("ECC_PROTOCOL")
    if protocol not in (None, "1", "2"):
        raise ValueError("ECC_PROTOCOL must be 1 or 2")
    legacy_resume = (root / "state.json").exists()
    if protocol == "2" or (protocol is None and not legacy_resume):
        from generic.client import main as generic_main
        return generic_main(root, key, campaign)
    if campaign != CAMPAIGN:
        raise ValueError("protocol 1 supports only ecc2k-130; select protocol 2")
    api = API(os.environ["ECC_API_URL"], key, campaign)
    state = ensure_identity(api, root)
    client = os.environ.get("ECC_CLIENT", "/opt/ecc2k130/ec2k-gpu")
    kat = os.environ.get("ECC_KAT", "/opt/ecc2k130/campaign-kat.hex")
    check = subprocess.run([client, "check", "--kat", kat], check=False)
    if check.returncode:
        raise RuntimeError("CUDA client host known-answer check failed")
    command = [client, "walk", "--run-id", str(api.run_id),
               "--dp-file", str(root / "dps.bin"), "--checkpoint", str(root / "state.ck"),
               "--dp-weight", str(api.dp_weight), "--max-iters", "4294967296",
               "--checkpoint-every", "60", "--device", os.environ.get("ECC_GPU", "0")]
    stop = False
    proc = None

    def on_signal(signo, frame):
        nonlocal stop
        stop = True
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    interval = max(10, int(os.environ.get("ECC_UPLOAD_SECONDS", "60")))
    spool_limit = max(64 * 1024 * 1024, int(os.environ.get("ECC_SPOOL_MAX_BYTES", str(512 * 1024 * 1024))))
    failed_sync = False
    with Heartbeat(api.url, key, protocol="v1", run_id=api.run_id, backend="cuda") as health:
        try:
            while not stop:
                pending = (root / "dps.bin").stat().st_size - state["uploaded_local"]
                if pending >= spool_limit:
                    health.update("backpressure")
                    log(f"run {api.run_id}: upload backlog reached {pending} bytes; GPU paused")
                    while pending >= spool_limit // 2 and not stop:
                        try:
                            upload_dps(api, root, state)
                            upload_checkpoint(api, root, state)
                            failed_sync = False
                            health.update("computing")
                        except Exception as exc:
                            failed_sync = True
                            health.update("upload_retry")
                            log(f"run {api.run_id}: backlog retained ({type(exc).__name__}: {exc})")
                        pending = (root / "dps.bin").stat().st_size - state["uploaded_local"]
                        if pending >= spool_limit // 2:
                            time.sleep(30)
                    continue
                health.update("computing")
                log(f"run {api.run_id}: starting sigma GPU walk")
                proc = subprocess.Popen(command)
                next_upload = time.monotonic()
                while proc.poll() is None and not stop:
                    if time.monotonic() >= next_upload:
                        try:
                            upload_dps(api, root, state)
                            upload_checkpoint(api, root, state)
                            failed_sync = False
                            health.update("computing")
                        except Exception as exc:
                            failed_sync = True
                            health.update("upload_retry")
                            log(f"run {api.run_id}: upload pending ({type(exc).__name__}: {exc})")
                        next_upload = time.monotonic() + interval
                    pending = (root / "dps.bin").stat().st_size - state["uploaded_local"]
                    if pending >= spool_limit:
                        proc.send_signal(signal.SIGTERM)
                        proc.wait(timeout=600)
                        break
                    time.sleep(1)
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=600)
                if proc.returncode:
                    raise RuntimeError(f"GPU client exited {proc.returncode}")
                if stop:
                    break
                # A stopped GPU is restarted only from its checkpoint and same run ID.
                if not (root / "state.ck").is_file():
                    raise RuntimeError("GPU stopped without a checkpoint")
            for attempt in range(3):
                try:
                    upload_dps(api, root, state)
                    upload_checkpoint(api, root, state, force=True)
                    failed_sync = False
                    break
                except Exception as exc:
                    failed_sync = True
                    log(f"run {api.run_id}: final upload attempt {attempt + 1} failed ({type(exc).__name__}: {exc})")
                    time.sleep(2)
        finally:
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=600)
    log(f"run {api.run_id}: client exit={proc.returncode if proc else 0} upload_pending={failed_sync}")
    return 1 if failed_sync else 0


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            from generic.check import worker_main
            raise SystemExit(worker_main())
        if sys.argv[1:]:
            raise ValueError("usage: client.py [--check]")
        raise SystemExit(main())
    except Exception as exc:
        print(f"public worker failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
