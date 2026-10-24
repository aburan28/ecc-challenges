#!/usr/bin/env python3
"""Run bounded Modal L4 contributor canaries for the nine requested curves."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CURVES = ("ecc2k-130", "ecc2-131", "ecc2k-163", "ecc2-163", "ecc2-191",
          "ecc2k-238", "ecc2-238", "ecc2k-358", "ecc2-353")
IMAGE = re.compile(r"(?:public\.ecr\.aws/o1a3z1g6|ghcr\.io/aburan28)/ecc-challenges-worker@sha256:[0-9a-f]{64}\Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--key-file", type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--checkpoint-only", action="store_true")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if not IMAGE.fullmatch(a.image):
        p.error("image must be an approved immutable public worker digest")
    if a.apply:
        if a.key_file is None or not a.key_file.is_file():
            p.error("--apply requires a dedicated mode-0600 key file")
        if stat.S_IMODE(a.key_file.stat().st_mode) & 0o077:
            p.error("key file must have mode 0600")
        modal = shutil.which("modal")
        if modal is None:
            p.error("Modal CLI is unavailable")
    a.out.mkdir(parents=True, exist_ok=False)
    plan = {"format": "nine-binary-modal-contributor-plan-v1",
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                      cwd=ROOT, text=True).strip(),
            "launcher_sha256": sha(ROOT / "scripts/check_modal_contributor.py"),
            "boot_sha256": sha(ROOT / "scripts/contributor_canary_boot.py"),
            "image": a.image, "campaigns": CURVES, "backend": "cuda", "gpu": "L4",
            "lanes": 256, "concurrency": 1, "max_sandbox_seconds": 1200,
            "requires_production_dp": not a.checkpoint_only,
            "mode": "apply" if a.apply else "plan"}
    (a.out / "plan.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
    print(json.dumps(plan, sort_keys=True), flush=True)
    if not a.apply:
        return
    rows = []
    for campaign in CURVES:
        destination = a.out / (campaign + ".json")
        command = [modal, "run",
                   "scripts/check_modal_contributor.py", "--key-file", str(a.key_file),
                   "--out", str(destination), "--image", a.image,
                   "--campaign", campaign, "--backend", "cuda", "--lanes", "256"]
        if a.checkpoint_only:
            command.append("--no-require-dp")
        try:
            done = subprocess.run(command, cwd=ROOT, capture_output=True,
                                  timeout=1500)
            row = {"campaign": campaign, "returncode": done.returncode,
                   "stdout_sha256": hashlib.sha256(done.stdout).hexdigest(),
                   "stderr_sha256": hashlib.sha256(done.stderr).hexdigest(),
                   "receipt": destination.name if destination.exists() else None}
        except subprocess.TimeoutExpired as error:
            row = {"campaign": campaign, "returncode": None,
                   "timeout_seconds": 1500,
                   "stdout_sha256": hashlib.sha256(error.stdout or b"").hexdigest(),
                   "stderr_sha256": hashlib.sha256(error.stderr or b"").hexdigest(),
                   "receipt": destination.name if destination.exists() else None}
        if destination.exists():
            receipt = json.loads(destination.read_text())
            row["ok"] = bool(receipt.get("ok"))
            row["closure_verified"] = bool(receipt.get("closure_verified"))
            row["run_id"] = (receipt.get("result") or {}).get("run_id")
            row["indexed_records"] = (receipt.get("result") or {}).get("production_records_indexed")
        rows.append(row)
        (a.out / "summary.json").write_text(json.dumps(
            {"format": "nine-binary-modal-contributor-summary-v1", "rows": rows,
             "complete": len(rows) == len(CURVES),
             "ok": len(rows) == len(CURVES) and all(r.get("ok") for r in rows)},
            sort_keys=True, indent=2) + "\n")
        print(json.dumps(row, sort_keys=True), flush=True)
        # A missing receipt indicates launcher or provider failure shared by
        # subsequent curves. Preserve the first failure for repair.
        if not destination.exists():
            break
    if len(rows) != len(CURVES) or not all(row.get("ok") for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
