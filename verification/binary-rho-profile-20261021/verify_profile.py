"""Verify matched L4 worker outputs and CUDA event telemetry from raw files."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "verification/all-curves-20261016/xtime-20261016/main-adaptation"))
from verify_l4_receipt import check  # noqa: E402


def verify(folder: Path) -> None:
    check(folder)
    receipt = json.loads((folder / "receipt.json").read_text())
    assert len(receipt["rows"]) == 54
    assert len(receipt["comparisons"]) == 18
    assert all(receipt["compiler_resource_usage"][arm]["returncode"] == 0
               for arm in ("baseline", "candidate"))
    with tarfile.open(folder / "raw.tar.gz", "r:gz") as archive:
        for row in receipt["rows"]:
            path = (f"raw/{row['campaign']}/{row['repeat']:02d}-"
                    f"{row['position']}-{row['arm']}/stderr.txt")
            member = archive.extractfile(path)
            assert member is not None
            lines = [line for line in member.read().decode().splitlines()
                     if line.startswith("ECC_PROFILE_CUDA_KERNEL_MS=")]
            assert len(lines) == 1
            measured = float(lines[0].split("=", 1)[1])
            assert measured > 0 and measured == row["cuda_kernel_ms"]
    print(f"{folder}: verified CUDA telemetry and compiler output")


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        verify(Path(argument))
