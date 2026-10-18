"""Client behaviour that differs between Linux, macOS and Windows."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

import generic.client as client
import generic.runtime as runtime


def test_state_directory_lock_is_exclusive(tmp_path):
    held = client.lock_state_directory(tmp_path)
    try:
        with pytest.raises(OSError):
            client.lock_state_directory(tmp_path)
    finally:
        held.close()


def test_atomic_write_replaces_and_restricts(tmp_path):
    path = tmp_path / "generic-state.json"
    client.atomic(path, {"cursor": 1})
    client.atomic(path, {"cursor": 2})
    assert json.loads(path.read_text()) == {"cursor": 2}
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_engine_is_isolated_from_console_signals():
    if os.name == "nt":
        assert runtime.ISOLATE_ENGINE == {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        assert runtime.ISOLATE_ENGINE == {"start_new_session": True}


def test_probe_reports_real_engine_and_tolerates_missing(engine, tmp_path):
    assert "cpu" in client.probe_engine(engine)["backends"]
    assert client.probe_engine(tmp_path / "no-such-engine") == {}


@pytest.mark.parametrize("report, expected", [
    ({"backends": ["cpu", "metal"], "metal_devices": ["Apple M2"]}, "metal"),
    ({"backends": ["cpu", "metal"], "metal_devices": []}, "cpu"),
    ({"backends": ["cpu"]}, "cpu"),
    ({}, "cpu"),
])
def test_macos_auto_backend_follows_engine_probe(monkeypatch, report, expected):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(client, "probe_engine", lambda engine: report)
    assert client.detect_backend() == expected


def test_gpu_backends_share_lane_default(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    assert client.default_lanes("metal") == client.default_lanes("cuda") == 64
    assert client.default_lanes("cpu") == 12
