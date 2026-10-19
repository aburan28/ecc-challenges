"""The image entry point checks real curves without enrollment or state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def check_environment(engine, tmp_path, backend):
    env = dict(os.environ)
    env.pop("ECC_API_KEY", None)
    # The subprocess cannot inherit conftest's in-process sys.path additions.
    # Native platform jobs intentionally install only pytest, not the service.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(ROOT / "src"), env.get("PYTHONPATH"))))
    env.update(ECC_GENERIC_ENGINE=str(engine), ECC_BACKEND=backend,
               ECC_API_KEY_FILE=str(tmp_path / "missing-key"),
               ECC_API_URL="https://127.0.0.1:9",
               ECC_STATE_DIR=str(tmp_path / "worker-state"),
               ECC_CHECK_OUT=str(tmp_path / "controls"))
    return env


def test_check_entry_point_covers_all_curves_without_key_or_worker_state(engine, tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "worker/client.py"), "--check"],
        env=check_environment(engine, tmp_path, "cpu"),
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["campaigns_checked"] == 24
    assert summary["checks"] == 168
    assert summary["backends_checked"] == ["cpu"]
    receipt = json.loads((tmp_path / "controls/receipt.json").read_text())
    assert receipt["all_24_campaigns"]
    assert receipt["engine_sha256"]["cpu"] == hashlib.sha256(engine.read_bytes()).hexdigest()
    assert not (tmp_path / "worker-state").exists()


def test_check_explicit_missing_backend_fails_without_cpu_fallback(engine, probe, tmp_path):
    missing = next(b for b in ("cuda", "metal") if b not in probe["backends"])
    result = subprocess.run(
        [sys.executable, str(ROOT / "worker/client.py"), "--check"],
        env=check_environment(engine, tmp_path, missing),
        capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert f"requested {missing} backend is unavailable" in result.stderr
    assert not (tmp_path / "controls").exists()
    assert not (tmp_path / "worker-state").exists()
