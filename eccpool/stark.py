"""Bridge to the ``eccstark`` Rust binary (STARK prover/verifier, native walker).

The binary speaks line-delimited JSON on stdin/stdout. ``StarkEngine`` keeps
a small pool of long-lived processes so the server can verify proofs from
several request threads at once.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from .curvegen import GOLDILOCKS
from .walk import WalkSpec


def find_binary() -> str | None:
    env = os.environ.get("ECCPOOL_STARK_BIN")
    if env:
        return env
    local = Path(__file__).resolve().parent.parent / "stark" / "target" / "release" / "eccstark"
    if local.exists():
        return str(local)
    return shutil.which("eccstark")


def supports(spec: WalkSpec) -> str | None:
    """None if STARK mode can prove walks for this spec, else the reason."""
    if spec.curve.p != GOLDILOCKS:
        return "STARK verification needs a curve over the Goldilocks field 2^64 - 2^32 + 1"
    if spec.r > 64:
        return "STARK verification supports r <= 64"
    if spec.dp_bits + spec.r.bit_length() > 60:
        return "dp_bits too large for STARK verification"
    return None


def walk_params(spec: WalkSpec) -> dict:
    return {"dp_bits": spec.dp_bits,
            "mults": [[hex(R[0]), hex(R[1])] for _, _, R in spec.multipliers]}


class _Proc:
    def __init__(self, binary: str) -> None:
        self.p = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1)

    def call(self, req: dict) -> dict:
        if self.p.poll() is not None:
            raise RuntimeError("eccstark process exited")
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("eccstark process died")
        return json.loads(line)

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


class StarkEngine:
    def __init__(self, binary: str | None = None, procs: int = 0) -> None:
        self.binary = binary or find_binary()
        if not self.binary:
            raise RuntimeError("eccstark binary not found: build it with "
                               "`cargo build --release --manifest-path stark/Cargo.toml` "
                               "or set ECCPOOL_STARK_BIN")
        self._n = procs or max(1, min(4, os.cpu_count() or 1))
        self._idle: queue.Queue[_Proc] = queue.Queue()
        self._all: list[_Proc] = []
        self._lock = threading.Lock()

    def _call(self, req: dict) -> dict:
        try:
            proc = self._idle.get_nowait()
        except queue.Empty:
            with self._lock:
                if len(self._all) < self._n:
                    proc = _Proc(self.binary)
                    self._all.append(proc)
                else:
                    proc = None
            if proc is None:
                proc = self._idle.get()
        try:
            resp = proc.call(req)
        except Exception:
            with self._lock:
                self._all.remove(proc)
            proc.close()
            raise
        self._idle.put(proc)
        return resp

    def verify(self, spec: WalkSpec, start, end, steps: int, end_is_dp: bool,
               proof_b64: str) -> str | None:
        """None if the proof is valid, else the reason."""
        resp = self._call({"cmd": "verify", **walk_params(spec),
                           "start": [hex(start[0]), hex(start[1])],
                           "end": [hex(end[0]), hex(end[1])], "steps": steps,
                           "end_is_dp": end_is_dp, "proof": proof_b64})
        return None if resp.get("ok") else resp.get("error", "invalid proof")

    def prove(self, spec: WalkSpec, start, steps: int, end_is_dp: bool) -> tuple[tuple[int, int], str]:
        resp = self._call({"cmd": "prove", **walk_params(spec),
                           "start": [hex(start[0]), hex(start[1])], "steps": steps,
                           "end_is_dp": end_is_dp})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error"))
        return (int(resp["end"][0], 16), int(resp["end"][1], 16)), resp["proof"]

    def work(self, spec: WalkSpec, key_id: str, nonce: str, prove: bool) -> dict | None:
        """Native walk from the key-bound start to its DP (optionally proven)."""
        a0, b0, start = spec.start(key_id, nonce)
        resp = self._call({"cmd": "work", **walk_params(spec),
                           "coeffs": [[hex(c), hex(d)] for c, d, _ in spec.multipliers],
                           "n": hex(spec.curve.n), "start": [hex(start[0]), hex(start[1])],
                           "a0": hex(a0), "b0": hex(b0), "max_len": spec.max_walk_len,
                           "checkpoint_interval": 0 if prove else spec.checkpoint_interval,
                           "prove": prove})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error"))
        return resp if resp.get("length") else None

    def close(self) -> None:
        with self._lock:
            for p in self._all:
                p.close()
            self._all.clear()


_engine: StarkEngine | None = None
_engine_lock = threading.Lock()


def engine() -> StarkEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = StarkEngine()
        return _engine
