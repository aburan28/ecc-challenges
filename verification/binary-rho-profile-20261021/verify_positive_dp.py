"""Replay the GPU parity receipt against frozen CPU outputs and positive DP1 cases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tarfile


NINE = {"ecc2k-130", "ecc2-131", "ecc2k-163", "ecc2-163", "ecc2-191",
        "ecc2k-238", "ecc2-238", "ecc2k-358", "ecc2-353"}
ARCHIVE_SHA256 = "ce502b916d286a7204430e598f4117ea1c17e8dfb4f6aa991a4edf1168ef4629"
CORE_SHA256 = "b20f3ae4e47ba25576a4353d374a131333f1b8584c42ca39925baf24c381999a"
ENGINE_SHA256 = "87211d525a18469c59e96fde2461d66d38c22a9b0a2362744ab8e82173443684"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(receipt_path: Path, archive_path: Path) -> None:
    assert sha(archive_path.read_bytes()) == ARCHIVE_SHA256
    receipt = json.loads(receipt_path.read_text())
    assert receipt["format"] == "cuda-native-correctness-v1"
    assert receipt["core_source_sha256"] == CORE_SHA256
    assert receipt["engine_source_sha256"] == ENGINE_SHA256
    assert receipt["campaigns"] == 24 and receipt["checks"] == 168
    assert len(receipt["rows"]) == 168
    positive = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        names = {member.name: member for member in archive.getmembers()}
        prefix = "eccjob03-xtime-final-cpu-controls-20261016/"

        def read(path: str) -> bytes | None:
            member = names.get(prefix + path)
            if member is None:
                return None
            stream = archive.extractfile(member)
            assert stream is not None
            return stream.read()

        for row in receipt["rows"]:
            path = f"{row['campaign']}/{row['case']}/"
            job, inp = read(path + "job.bin"), read(path + "in.bin")
            assert job is not None and inp is not None
            assert sha(job + inp) == row["input_sha256"]
            output, dp = read(path + "out.bin"), read(path + "dp.bin")
            if output is None:
                assert dp is None and row["returncode"] != 0
                assert row["state_sha256"] is None and row["dp_sha256"] is None
            else:
                assert dp is not None and row["returncode"] == 0
                assert sha(output) == row["state_sha256"]
                assert sha(dp) == row["dp_sha256"]
                if row["case"] == "mixed-dp1-cpu":
                    assert len(dp) > 0 and len(dp) % 216 == 0
                    positive[row["campaign"]] = len(dp) // 216
    assert len(positive) == 24
    assert set(positive) >= NINE
    assert sum(positive[curve] for curve in NINE) == 137
    print(f"verified {receipt['checks']} CUDA controls, {len(positive)} positive DP1 curves, "
          f"137 distinguished points in the nine requested curves")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
