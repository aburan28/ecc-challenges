from __future__ import annotations

import sys

from scripts import check_sandbox_runtime as smoke


def test_failed_engine_check_removes_disposable_volume(monkeypatch, capsys):
    calls = []

    def fake_checked(*args):
        calls.append(args)
        if args[:2] == ("docker", "info"):
            return '{"runsc": {}}'
        if args[:3] == ("docker", "volume", "create"):
            return args[3]
        if args[:2] == ("docker", "run") and smoke.ENGINE in args:
            raise RuntimeError("engine failed")
        return ""

    monkeypatch.setattr(smoke, "checked", fake_checked)
    monkeypatch.setattr(sys, "argv", [
        "check_sandbox_runtime.py", "--runtime", "runsc", "--image",
        "example.invalid/worker@sha256:" + "a" * 64,
    ])

    assert smoke.main() == 1
    assert calls[-1][:3] == ("docker", "volume", "rm")
    assert "engine failed" in capsys.readouterr().err
