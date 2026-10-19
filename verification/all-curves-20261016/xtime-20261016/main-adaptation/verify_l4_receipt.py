"""Replay ECCJOB03 L4 receipt hashes, step counters, and paired equality."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import tarfile


def check(folder: Path) -> None:
    receipt = json.loads((folder / "receipt.json").read_text())
    assert receipt["format"] == "eccjob03-binary-l4-matched-v1"
    rows = receipt["rows"]
    assert len(rows) == len(receipt["comparisons"]) * 3
    assert "failure" not in receipt
    with tarfile.open(folder / "raw.tar.gz", "r:gz") as archive:
        names = set(archive.getnames())

        def read(name: str) -> bytes:
            full = "raw/" + name
            assert full in names, full
            file = archive.extractfile(full)
            assert file is not None, full
            return file.read()

        def sha(data: bytes) -> str:
            return hashlib.sha256(data).hexdigest()

        for row in rows:
            curve = row["campaign"]
            job = read(f"{curve}/job.bin")
            inp = read(f"{curve}/in.bin")
            path = f"{curve}/{row['repeat']:02d}-{row['position']}-{row['arm']}"
            out = read(path + "/out.bin")
            dp = read(path + "/dp.bin")
            read(path + "/stdout.txt")
            read(path + "/stderr.txt")
            assert job[:8] == b"ECCJOB03" and len(job) == 6568
            assert len(inp) == len(out) == receipt["lanes"] * 216
            assert len(dp) % 216 == 0
            assert row["returncode"] == 0
            assert sha(job) == row["job_sha256"]
            assert sha(inp) == row["input_sha256"]
            assert sha(out) == row["output_sha256"]
            assert sha(dp) == row["dp_sha256"]
            assert len(dp) // 216 == row["dp_count"]
            before = sum(struct.unpack_from("<Q", inp, i + 200)[0]
                         for i in range(0, len(inp), 216))
            after = sum(struct.unpack_from("<Q", out, i + 200)[0]
                        for i in range(0, len(out), 216))
            assert after - before == row["actual_steps"]
            assert row["actual_steps"] == receipt["lanes"] * receipt["steps_per_round"]

    for comparison in receipt["comparisons"]:
        pair = [r for r in rows if r["campaign"] == comparison["campaign"]
                and r["repeat"] == comparison["repeat"]]
        assert len(pair) == 3
        assert [r["arm"] for r in sorted(pair, key=lambda r: r["position"])] == [
            "baseline", "candidate", "baseline"]
        for key in ("actual_steps", "output_sha256", "dp_sha256"):
            assert len({r[key] for r in pair}) == 1
    print(f"{folder}: verified {len(rows)} rows and {len(receipt['comparisons'])} pairs")


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        check(Path(argument))
