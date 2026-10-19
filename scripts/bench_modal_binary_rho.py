"""Matched, bounded L4 ECCJOB03 invocation benchmark for nine binary curves.

Usage:
  ECC_BASELINE_HEADER=/path/to/baseline-ec_core.h \
  ECC_NATIVE_CONTROLS=/path/to/native-controls \
  ECC_BENCH_OUT=/path/to/new/output modal run scripts/bench_modal_binary_rho.py

The two arms compile the same current engine source with different pinned
arithmetic headers in one image. The remote function returns raw inputs/outputs
and a write-once receipt. Timings include process startup, validation, transfers,
kernel execution, and output serialization.
"""
from __future__ import annotations

from pathlib import Path
import os
import modal


CURVES = ("ecc2k-130", "ecc2-131", "ecc2k-163", "ecc2-163", "ecc2-191",
          "ecc2k-238", "ecc2-238", "ecc2k-358", "ecc2-353")

app = modal.App("ecc-challenges-binary-rho-throughput")
# Modal imports this module again inside the container. Only the client has
# paths to the local source and frozen controls used to build the image.
if modal.is_local():
    root = Path(__file__).resolve().parents[1]
    baseline_header = Path(os.environ["ECC_BASELINE_HEADER"]).resolve()
    controls = Path(os.environ["ECC_NATIVE_CONTROLS"]).resolve()
    out = Path(os.environ["ECC_BENCH_OUT"]).resolve()
    image = (modal.Image.from_registry("nvidia/cuda:13.0.2-devel-ubuntu24.04", add_python="3.12")
             .add_local_file(root / "worker/generic/engine.cu", "/src/baseline/engine.cu", copy=True)
             .add_local_file(baseline_header, "/src/baseline/ec_core.h", copy=True)
             .add_local_file(root / "worker/generic/engine.cu", "/src/candidate/engine.cu", copy=True)
             .add_local_file(root / "worker/generic/ec_core.h", "/src/candidate/ec_core.h", copy=True))
    for curve in CURVES:
        for filename in ("job.bin", "in.bin"):
            image = image.add_local_file(
                controls / curve / "mixed-dp40-cpu" / filename,
                f"/controls/{curve}/mixed-dp40-cpu/{filename}", copy=True)
    image = image.run_commands(
        "nvcc -std=c++17 -O2 -gencode arch=compute_80,code=compute_80 /src/baseline/engine.cu -o /opt/ecc-baseline",
        "nvcc -std=c++17 -O2 -gencode arch=compute_80,code=compute_80 /src/candidate/engine.cu -o /opt/ecc-candidate",
        "/opt/ecc-baseline --self-test", "/opt/ecc-candidate --self-test")
else:
    # The deployed image above is already bound to the remote function.
    image = modal.Image.debian_slim()


@app.function(image=image, gpu="L4", timeout=900)
def measure(lanes: int = 32, steps: int = 64, repeats: int = 2,
            launcher_sha256: str = ""):
    import hashlib
    import io
    import json
    import platform
    import statistics
    import struct
    import subprocess
    import tarfile
    import tempfile
    import time

    if not 1 <= lanes <= 4096 or not 1 <= steps <= 1048576 or not 1 <= repeats <= 8:
        raise ValueError("workload outside bounded engine limits")
    sha = lambda data: hashlib.sha256(data).hexdigest()
    device = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,uuid,driver_version,clocks.sm,clocks.mem,temperature.gpu,power.draw",
         "--format=csv,noheader"], capture_output=True, text=True,
        check=True, timeout=30).stdout.strip()
    sources = {arm: {name: sha(Path(f"/src/{arm}/{name}").read_bytes())
                     for name in ("engine.cu", "ec_core.h")}
               for arm in ("baseline", "candidate")}
    binaries = {arm: sha(Path(f"/opt/ecc-{arm}").read_bytes())
                for arm in ("baseline", "candidate")}
    receipt = {"format": "eccjob03-binary-l4-matched-v1", "device": device,
               "system": platform.uname()._asdict(), "source_sha256": sources,
               "binary_sha256": binaries, "launcher_sha256": launcher_sha256,
               "lanes": lanes,
               "steps_per_round": steps, "repeats": repeats,
               "timing_boundary": "full worker process invocation including validation, transfers, kernel and output",
               "rows": [], "comparisons": []}
    def pack_raw(root: Path) -> bytes:
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            tar.add(root, arcname="raw")
        return tar_buffer.getvalue()

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        for curve in CURVES:
            folder = Path("/controls") / curve / "mixed-dp40-cpu"
            job = bytearray((folder / "job.bin").read_bytes())
            original = (folder / "in.bin").read_bytes()
            if len(job) != 6568 or job[:8] != b"ECCJOB03" or len(original) % 216:
                raise ValueError(f"bad frozen control for {curve}")
            states = [original[i:i+216] for i in range(0, len(original), 216)]
            states = [s for s in states if struct.unpack_from("<I", s, 208)[0] == 1]
            if not states:
                raise ValueError(f"no active frozen controls for {curve}")
            struct.pack_into("<I", job, 24, lanes)
            struct.pack_into("<I", job, 28, steps)
            expanded = []
            for i in range(lanes):
                state = bytearray(states[i % len(states)])
                struct.pack_into("<Q", state, 192, i + 1)
                expanded.append(state)
            payload = b"".join(expanded)
            before = sum(struct.unpack_from("<Q", payload, i + 200)[0]
                         for i in range(0, len(payload), 216))
            case = root / curve
            case.mkdir()
            (case / "job.bin").write_bytes(job)
            (case / "in.bin").write_bytes(payload)
            for repeat in range(repeats):
                # Two identical baseline invocations bracket the candidate
                # in each repeat and expose local invocation-time drift.
                order = ("baseline", "candidate", "baseline")
                rows = []
                for position, arm in enumerate(order):
                    trial = case / f"{repeat:02d}-{position}-{arm}"
                    trial.mkdir()
                    out, dp = trial / "out.bin", trial / "dp.bin"
                    command = [f"/opt/ecc-{arm}", "--backend", "cuda",
                               str(case / "job.bin"), str(case / "in.bin"),
                               str(out), str(dp)]
                    start = time.perf_counter_ns()
                    try:
                        process = subprocess.run(command, capture_output=True, timeout=120)
                    except subprocess.TimeoutExpired as error:
                        (trial / "stdout.txt").write_bytes(error.stdout or b"")
                        (trial / "stderr.txt").write_bytes(error.stderr or b"")
                        receipt["rows"].append({
                            "campaign": curve, "repeat": repeat,
                            "position": position, "arm": arm, "command": command,
                            "timeout_seconds": 120,
                            "elapsed_ns": time.perf_counter_ns() - start,
                            "job_sha256": sha(job), "input_sha256": sha(payload)})
                        receipt["failure"] = f"{curve} {arm} timed out after 120 seconds"
                        return receipt, pack_raw(root)
                    elapsed = time.perf_counter_ns() - start
                    (trial / "stdout.txt").write_bytes(process.stdout)
                    (trial / "stderr.txt").write_bytes(process.stderr)
                    row = {"campaign": curve, "repeat": repeat, "position": position,
                           "arm": arm, "command": command, "returncode": process.returncode,
                           "elapsed_ns": elapsed, "job_sha256": sha(job),
                           "input_sha256": sha(payload)}
                    if process.returncode:
                        receipt["rows"].append(row)
                        receipt["failure"] = (f"{curve} {arm} failed: "
                                              + process.stderr.decode(errors="replace"))
                        return receipt, pack_raw(root)
                    if not out.is_file() or not dp.is_file():
                        receipt["rows"].append(row)
                        receipt["failure"] = f"{curve} {arm} omitted an output file"
                        return receipt, pack_raw(root)
                    output, points = out.read_bytes(), dp.read_bytes()
                    if len(output) != len(payload) or len(points) % 216:
                        receipt["rows"].append(row)
                        receipt["failure"] = f"invalid native output length: {curve} {arm}"
                        return receipt, pack_raw(root)
                    after = sum(struct.unpack_from("<Q", output, i + 200)[0]
                                for i in range(0, len(output), 216))
                    row.update({"actual_steps": after - before,
                                "output_sha256": sha(output), "dp_sha256": sha(points),
                                "dp_count": len(points) // 216})
                    receipt["rows"].append(row)
                    rows.append(row)
                for key in ("actual_steps", "output_sha256", "dp_sha256"):
                    if len({row[key] for row in rows}) != 1:
                        receipt["failure"] = f"matched outputs differ: {curve} {key}"
                        return receipt, pack_raw(root)
                baseline_ns = [r["elapsed_ns"] for r in rows if r["arm"] == "baseline"]
                candidate_ns = next(r["elapsed_ns"] for r in rows if r["arm"] == "candidate")
                receipt["comparisons"].append({
                    "campaign": curve, "repeat": repeat,
                    "baseline_bracket_ns": baseline_ns,
                    "candidate_ns": candidate_ns,
                    "candidate_over_baseline_median": candidate_ns / statistics.median(baseline_ns),
                    "actual_steps": rows[0]["actual_steps"]})
        return receipt, pack_raw(root)


@app.local_entrypoint()
def main(lanes: int = 32, steps: int = 64, repeats: int = 2):
    import hashlib
    import json
    if out.exists():
        raise ValueError(f"output already exists: {out}")
    launcher_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    receipt, raw = measure.remote(lanes, steps, repeats, launcher_sha256)
    out.mkdir(parents=True, exist_ok=False)
    (out / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    (out / "raw.tar.gz").write_bytes(raw)
    if receipt.get("failure"):
        raise RuntimeError(f"benchmark failed; raw evidence retained in {out}: {receipt['failure']}")
    print(json.dumps({"receipt": str(out / "receipt.json"),
                      "raw": str(out / "raw.tar.gz"),
                      "rows": len(receipt["rows"]),
                      "comparisons": len(receipt["comparisons"])}))
