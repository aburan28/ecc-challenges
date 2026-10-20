"""Replay matched ECCJOB03 binary walks and retain raw timing receipts.

This measures a complete native worker invocation, including validation and
file I/O. It is an exploratory comparison unless the host isolation contract
in AGENTS.md is separately met. No target recovery rate is inferred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import struct
import subprocess
import tarfile
import time


CURVES = (
    "ecc2k-130", "ecc2-131", "ecc2k-163", "ecc2-163", "ecc2-191",
    "ecc2k-238", "ecc2-238", "ecc2k-358", "ecc2-353",
)
STATE_BYTES = 216
JOB_BYTES = 6568


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def workload(archive: tarfile.TarFile, name: str, lanes: int, steps: int) -> tuple[bytes, bytes]:
    prefix = f"./{name}/mixed-dp40-cpu/"
    def read(filename: str) -> bytes:
        member = archive.getmember(prefix + filename)
        if not member.isfile():
            raise ValueError(f"non-file archive member: {member.name}")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError(f"unreadable archive member: {member.name}")
        return stream.read()

    job = bytearray(read("job.bin"))
    states = read("in.bin")
    if len(job) != JOB_BYTES or job[:8] != b"ECCJOB03" or len(states) % STATE_BYTES:
        raise ValueError(f"invalid frozen control: {name}")
    original_lanes = struct.unpack_from("<I", job, 24)[0]
    if len(states) != original_lanes * STATE_BYTES:
        raise ValueError(f"control state count mismatch: {name}")
    source = [states[i:i + STATE_BYTES] for i in range(0, len(states), STATE_BYTES)]
    source = [row for row in source if struct.unpack_from("<I", row, 208)[0] == 1]
    if not source:
        raise ValueError(f"no active states: {name}")
    struct.pack_into("<I", job, 24, lanes)
    struct.pack_into("<I", job, 28, steps)
    rows = []
    for i in range(lanes):
        row = bytearray(source[i % len(source)])
        struct.pack_into("<Q", row, 192, i + 1)
        rows.append(row)
    return bytes(job), b"".join(rows)


def run_once(binary: Path, folder: Path, job: bytes, states: bytes) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    job_path, in_path = folder / "job.bin", folder / "in.bin"
    out_path, dp_path = folder / "out.bin", folder / "dp.bin"
    job_path.write_bytes(job)
    in_path.write_bytes(states)
    command = [str(binary), "--backend", "cpu", str(job_path), str(in_path),
               str(out_path), str(dp_path)]
    started_ns = time.perf_counter_ns()
    process = subprocess.run(command, capture_output=True)
    elapsed_ns = time.perf_counter_ns() - started_ns
    (folder / "stdout.txt").write_bytes(process.stdout)
    (folder / "stderr.txt").write_bytes(process.stderr)
    record = {"command": command, "returncode": process.returncode,
              "elapsed_ns": elapsed_ns, "stdout_sha256": sha256(process.stdout),
              "stderr_sha256": sha256(process.stderr)}
    if process.returncode == 0:
        output = out_path.read_bytes()
        dp = dp_path.read_bytes()
        if len(output) != len(states) or len(dp) % STATE_BYTES:
            raise ValueError("invalid output lengths")
        before = sum(struct.unpack_from("<Q", states, i + 200)[0]
                     for i in range(0, len(states), STATE_BYTES))
        after = sum(struct.unpack_from("<Q", output, i + 200)[0]
                    for i in range(0, len(output), STATE_BYTES))
        record.update({"actual_steps": after - before,
                       "output_sha256": sha256(output), "dp_sha256": sha256(dp),
                       "dp_count": len(dp) // STATE_BYTES})
    (folder / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    if process.returncode:
        raise RuntimeError(f"worker failed; retained {folder}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--candidate-source", type=Path)
    parser.add_argument("--archive", type=Path, default=Path(
        "verification/all-curves-20261016/job03-controls.tar.gz"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lanes", type=int, default=32)
    parser.add_argument("--steps", type=int, default=32)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.lanes <= 4096 or not 1 <= args.steps <= 1048576 or args.repeats < 1:
        parser.error("lanes, steps, or repeats outside worker bounds")
    binaries = {"baseline": args.baseline.resolve()}
    if args.candidate:
        binaries["candidate"] = args.candidate.resolve()
    sources = {"baseline": args.baseline_source,
               "candidate": args.candidate_source}
    for binary in binaries.values():
        if not binary.is_file() or not os.access(binary, os.X_OK):
            parser.error(f"not an executable file: {binary}")
    args.out.mkdir(parents=True, exist_ok=False)
    receipt = {"format": "eccjob03-binary-rho-invocation-bench-v1",
               "scope": "exploratory CPU whole-invocation timing; no host isolation attested",
               "system": platform.uname()._asdict(),
               "archive_sha256": sha256(args.archive.read_bytes()),
               "binaries": {label: {"path": str(binary),
                                    "sha256": sha256(binary.read_bytes()),
                                    "source_sha256": sha256(sources[label].read_bytes())
                                        if sources[label] else None}
                            for label, binary in binaries.items()},
               "benchmark_source_sha256": sha256(Path(__file__).read_bytes()),
               "lanes": args.lanes, "steps_per_round": args.steps,
               "repeats": args.repeats, "rows": []}
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    with tarfile.open(args.archive, "r:gz") as archive:
        for name in CURVES:
            job, states = workload(archive, name, args.lanes, args.steps)
            rows = {label: [] for label in binaries}
            for repeat in range(args.repeats):
                order = list(binaries) if repeat % 2 == 0 else list(reversed(binaries))
                for label in order:
                    row = run_once(binaries[label], args.out / name /
                                   f"{repeat:02d}-{label}", job, states)
                    row.update({"campaign": name, "arm": label, "repeat": repeat,
                                "job_sha256": sha256(job), "input_sha256": sha256(states)})
                    rows[label].append(row)
                    receipt["rows"].append(row)
                    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            if "candidate" in rows:
                for base, candidate in zip(rows["baseline"], rows["candidate"]):
                    for key in ("output_sha256", "dp_sha256", "actual_steps"):
                        if base[key] != candidate[key]:
                            raise ValueError(f"{name} candidate differs on {key}; retained {args.out}")
            medians = {label: statistics.median(r["elapsed_ns"] for r in arm)
                       for label, arm in rows.items()}
            print(json.dumps({"campaign": name, "median_invocation_ns": medians,
                              "actual_steps": rows["baseline"][0]["actual_steps"]}), flush=True)
    print(json.dumps({"receipt": str(args.out / "receipt.json"),
                      "rows": len(receipt["rows"])}))


if __name__ == "__main__":
    main()
