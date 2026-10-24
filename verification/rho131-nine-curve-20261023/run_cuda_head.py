#!/usr/bin/env python3
"""Replay archived CPU controls on current source with a key-free Modal L4.

Plan mode verifies the archive without contacting Modal. --apply uses the
already configured provider profile; no contributor key is read or uploaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CURVES = ("ecc2k-130", "ecc2-131", "ecc2k-163", "ecc2-163", "ecc2-191",
          "ecc2k-238", "ecc2-238", "ecc2k-358", "ecc2-353")
SOURCES = {"engine_source_sha256": ROOT / "worker/generic/engine.cu",
           "core_source_sha256": ROOT / "worker/generic/ec_core.h",
           "fast131_source_sha256": ROOT / "worker/generic/fast131.h",
           "fast131_host_source_sha256": ROOT / "worker/generic/fast131_host.h"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_controls(destination: Path) -> dict:
    archive = HERE / "native-controls-all24.tar.zst"
    manifest = json.loads((HERE / "native-controls-all24.archive.json").read_text())
    if sha(archive) != manifest["sha256"]:
        raise ValueError("control archive digest differs")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise RuntimeError("zstd executable is required")
    process = subprocess.Popen([zstd, "-dc", str(archive)], stdout=subprocess.PIPE)
    count = total = 0
    seen: set[str] = set()
    receipt = None
    try:
        assert process.stdout is not None
        with tarfile.open(fileobj=process.stdout, mode="r|") as source:
            for member in source:
                name = PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or ".." in name.parts or
                        len(name.parts) < 2 or name.parts[0] != "native-controls-all24" or
                        member.name in seen or member.size > 1048576 or
                        member.uid != 0 or member.gid != 0):
                    raise ValueError("unsafe or duplicate control archive member")
                seen.add(member.name)
                count += 1
                total += member.size
                if count > manifest["files"] or total > 16 * 1048576:
                    raise ValueError("control archive exceeds bounded size")
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError("missing control archive payload")
                data = stream.read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError("control archive member size differs")
                target = destination.joinpath(*name.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(data)
                if name.parts[1:] == ("receipt.json",):
                    receipt = hashlib.sha256(data).hexdigest()
    finally:
        process.stdout.close()
        if process.wait() != 0:
            raise RuntimeError("zstd decompression failed")
    if count != manifest["files"] or receipt != manifest["receipt_sha256"]:
        raise ValueError("control archive coverage or receipt digest differs")
    campaigns = {p.name for p in destination.iterdir() if p.is_dir()}
    if len(campaigns) != 24 or not set(CURVES).issubset(campaigns):
        raise ValueError("control archive lacks required campaigns")
    return {"archive_sha256": manifest["sha256"], "files": count,
            "receipt_sha256": receipt, "campaigns": len(campaigns)}


def expected_cases(controls: Path) -> dict[tuple[str, str], dict]:
    expected = {}
    for campaign in sorted(p for p in controls.iterdir() if p.is_dir()):
        for case in sorted(p for p in campaign.iterdir() if p.is_dir() and p.name.endswith("-cpu")):
            job, incoming = (case / "job.bin").read_bytes(), (case / "in.bin").read_bytes()
            output, points = case / "out.bin", case / "dp.bin"
            success = output.is_file()
            if success != points.is_file():
                raise ValueError("incomplete CPU control output")
            if case.name == "mixed-dp1-cpu" and (not success or not points.read_bytes()):
                raise ValueError("missing positive distinguished-point control")
            expected[(campaign.name, case.name)] = {
                "input_sha256": hashlib.sha256(job + incoming).hexdigest(),
                "state_sha256": sha(output) if success else None,
                "dp_sha256": sha(points) if success else None,
                "success": success,
            }
    if len(expected) != 168:
        raise ValueError("expected 168 CPU control cases")
    return expected


def verify_receipt(receipt: dict | None, cases: dict[tuple[str, str], dict],
                   source_hashes: dict[str, str], code: int | None) -> bool:
    if not receipt or code != 0 or receipt.get("campaigns") != 24 or receipt.get("checks") != 168:
        return False
    if "L4" not in str(receipt.get("device", "")) or not re.fullmatch(
            r"[0-9a-f]{64}", str(receipt.get("engine_binary_sha256", ""))):
        return False
    if any(receipt.get(name) != digest for name, digest in source_hashes.items()):
        return False
    rows = receipt.get("rows")
    if not isinstance(rows, list) or len(rows) != len(cases):
        return False
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            return False
        identity = (row.get("campaign"), row.get("case"))
        if identity not in cases or identity in seen:
            return False
        seen.add(identity)
        expected = cases[identity]
        if any(row.get(name) != expected[name]
               for name in ("input_sha256", "state_sha256", "dp_sha256")):
            return False
        if (row.get("returncode") == 0) != expected["success"]:
            return False
    return seen == set(cases) and set(CURVES).issubset({name for name, _ in seen})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.out.exists():
        parser.error("output directory already exists")
    modal = shutil.which("modal") if args.apply else None
    if args.apply and modal is None:
        parser.error("Modal CLI is unavailable")
    with tempfile.TemporaryDirectory(prefix="ecc-rho131-cuda-controls-") as scratch:
        controls = Path(scratch) / "controls"
        controls.mkdir(mode=0o700)
        archive = extract_controls(controls)
        cases = expected_cases(controls)
        plan = {"format": "rho131-current-head-l4-control-plan-v1",
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                         cwd=ROOT, text=True).strip(),
                "source_sha256": {name: sha(path) for name, path in SOURCES.items()},
                "modal_launcher_sha256": sha(ROOT / "scripts/check_modal_curves.py"),
                "archive": archive, "gpu": "L4", "backend": "cuda",
                "expected_checks": 168, "expected_campaigns": 24,
                "required_curves": CURVES, "mode": "apply" if args.apply else "plan"}
        args.out.mkdir(parents=True, mode=0o700)
        (args.out / "plan.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
        if not args.apply:
            print(json.dumps(plan, sort_keys=True))
            return
        env = os.environ.copy()
        env["ECC_NATIVE_CONTROLS"] = str(controls)
        try:
            done = subprocess.run([modal, "run", "scripts/check_modal_curves.py"],
                                  cwd=ROOT, env=env, capture_output=True, timeout=1800)
            stdout, stderr, code = done.stdout, done.stderr, done.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr, code = error.stdout or b"", error.stderr or b"", None
        (args.out / "modal.stdout.log").write_bytes(stdout)
        (args.out / "modal.stderr.log").write_bytes(stderr)
        receipt_path = controls / "cuda-receipt.json"
        receipt = None
        if receipt_path.exists():
            shutil.copyfile(receipt_path, args.out / "cuda-receipt.json")
            receipt = json.loads(receipt_path.read_text())
        valid = verify_receipt(receipt, cases, plan["source_sha256"], code)
        result = {"format": "rho131-current-head-l4-control-result-v1", "ok": valid,
                  "modal_returncode": code, "receipt": "cuda-receipt.json" if receipt else None,
                  "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                  "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                  "source_sha256": plan["source_sha256"], "archive": archive}
        (args.out / "result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(json.dumps({"ok": valid, "receipt": result["receipt"],
                          "output": str(args.out)}, sort_keys=True))
        if not valid:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
