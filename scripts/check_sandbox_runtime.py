#!/usr/bin/env python3
"""Offline CPU worker smoke check for a configured Docker OCI runtime.

No gateway key is read. A fresh named volume is removed after the check.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid


ENGINE = r"""
import json, os, subprocess
engine = "/opt/ecc/generic-engine-cpu"
if not os.path.isfile(engine):
    engine = "/opt/ecc/generic-engine"
subprocess.run([engine, "--self-test"], check=True)
report = json.loads(subprocess.check_output([engine, "--probe"], text=True))
assert "cpu" in report["backends"], report
print(json.dumps({"engine": engine, "backends": report["backends"]}))
"""

CHECKPOINT = r"""
import json, os, stat, sys
from pathlib import Path
from generic.client import atomic, lock_state_directory
root = Path("/data")
path = root / "sandbox-smoke.json"
phase = sys.argv[1]
lock = lock_state_directory(root)
try:
    if phase == "write":
        assert not path.exists()
        atomic(path, {"cursor": 1, "purpose": "sandbox-runtime-smoke"})
    else:
        assert json.loads(path.read_text()) == {
            "cursor": 1, "purpose": "sandbox-runtime-smoke"}
        atomic(path, {"cursor": 2, "purpose": "sandbox-runtime-smoke"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    print(json.dumps({"cursor": json.loads(path.read_text())["cursor"],
                      "uid": os.getuid(), "mode": "0600"}))
finally:
    lock.close()
"""


def checked(*args: str) -> str:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} {args[1]} failed ({result.returncode}): "
            f"{result.stderr[-2000:]}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, help="Docker runtime name, e.g. runsc or kata")
    parser.add_argument("--image", required=True, help="Pinned Linux worker image at @sha256:digest")
    args = parser.parse_args()
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", args.image):
        parser.error("--image must use a full sha256 digest")
    try:
        runtimes = json.loads(checked("docker", "info", "--format", "{{json .Runtimes}}"))
        if args.runtime not in (runtimes or {}):
            raise RuntimeError(f"Docker runtime {args.runtime!r} is not registered")
        volume = "ecc-sandbox-smoke-" + uuid.uuid4().hex
        checked("docker", "volume", "create", volume)
        try:
            common = (
                "docker", "run", "--rm", "--runtime", args.runtime,
                "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev",
                "--volume", f"{volume}:/data", "--entrypoint", "python3",
            )
            # Only the disposable volume bootstrap runs as root. The worker
            # checks run with the image's unprivileged UID and no capabilities.
            checked(*common, "--user", "0:0", "--cap-drop", "ALL",
                    "--cap-add", "CHOWN", args.image, "-c",
                    "import os; os.chown('/data', 10001, 10001)")
            worker = (*common, "--user", "10001:10001", "--cap-drop", "ALL",
                      "--security-opt", "no-new-privileges",
                      "--env", "PYTHONPATH=/opt/ecc2k130", args.image, "-c")
            engine = checked(*worker, ENGINE)
            first = checked(*worker, CHECKPOINT, "write")
            second = checked(*worker, CHECKPOINT, "resume")
            print(json.dumps({
                "runtime": args.runtime, "image": args.image,
                "engine": json.loads(engine.splitlines()[-1]),
                "first": json.loads(first.splitlines()[-1]),
                "second": json.loads(second.splitlines()[-1]),
                "offline_only": True,
            }, sort_keys=True))
        finally:
            checked("docker", "volume", "rm", volume)
    except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
        print(f"sandbox runtime check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
