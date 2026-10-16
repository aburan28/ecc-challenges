"""Replay the frozen CPU parity cases on a real NVIDIA GPU through Modal.

Usage: ECC_NATIVE_CONTROLS=/path/to/check_native_curves/output modal run scripts/check_modal_curves.py
No contributor key is used and no cloud production database is touched.
"""
from pathlib import Path
import os
import modal

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = Path(os.environ.get("ECC_NATIVE_CONTROLS", "/controls")).resolve()
app = modal.App("ecc-challenges-native-correctness")
image = (modal.Image.from_registry("nvidia/cuda:13.0.2-devel-ubuntu24.04", add_python="3.12")
         .add_local_file(ROOT / "worker/generic/engine.cu", "/src/engine.cu", copy=True)
         .run_commands("nvcc -std=c++17 -O2 -gencode arch=compute_80,code=compute_80 /src/engine.cu -o /src/engine",
                       "/src/engine --self-test")
         .add_local_dir(CONTROLS, "/controls"))


@app.function(image=image, gpu="L4", timeout=900)
def verify():
    import hashlib
    import json
    import subprocess
    root = Path("/controls")
    device = subprocess.run(["nvidia-smi", "--query-gpu=name,uuid,driver_version", "--format=csv,noheader"],
                            capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    rows = []
    for campaign in sorted(p for p in root.iterdir() if p.is_dir()):
        for case in sorted(p for p in campaign.iterdir() if p.is_dir() and p.name.endswith("-cpu")):
            output, dps = Path("/tmp/out.bin"), Path("/tmp/dp.bin")
            output.unlink(missing_ok=True)
            dps.unlink(missing_ok=True)
            command = ["/src/engine", "--backend", "cuda", str(case / "job.bin"), str(case / "in.bin"), str(output), str(dps)]
            result = subprocess.run(command, capture_output=True, timeout=120)
            expected_success = (case / "out.bin").exists()
            if expected_success:
                if result.returncode or output.read_bytes() != (case / "out.bin").read_bytes() or dps.read_bytes() != (case / "dp.bin").read_bytes():
                    raise RuntimeError(f"CUDA parity failed: {campaign.name}/{case.name}: {result.stderr.decode(errors='replace')}")
            elif result.returncode == 0 or output.exists() or dps.exists():
                raise RuntimeError(f"CUDA malformed-input rejection failed: {campaign.name}/{case.name}")
            rows.append({"campaign": campaign.name, "case": case.name,
                         "input_sha256": hashlib.sha256((case / "job.bin").read_bytes() + (case / "in.bin").read_bytes()).hexdigest(),
                         "returncode": result.returncode,
                         "state_sha256": hashlib.sha256(output.read_bytes()).hexdigest() if expected_success else None,
                         "dp_sha256": hashlib.sha256(dps.read_bytes()).hexdigest() if expected_success else None})
    return {"format": "cuda-native-correctness-v1", "device": device,
            "engine_source_sha256": hashlib.sha256(Path('/src/engine.cu').read_bytes()).hexdigest(),
            "engine_binary_sha256": hashlib.sha256(Path('/src/engine').read_bytes()).hexdigest(),
            "campaigns": len({r['campaign'] for r in rows}), "checks": len(rows), "rows": rows}


@app.local_entrypoint()
def main():
    import json
    result = verify.remote()
    destination = CONTROLS / "cuda-receipt.json"
    destination.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"campaigns": result["campaigns"], "checks": result["checks"], "device": result["device"], "receipt": str(destination)}))
