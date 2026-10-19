"""Finite native correctness controls for every Certicom instance.

Run after building the CPU and CUDA engines. This script retains all inputs
and outputs under a new directory, including artifacts from a failed check.
It reports correctness coverage only; it measures no performance.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

from ecc_challenges.multicurve.instances import all_instances

WIDTH = 48
ROW = 216
BOUNDARIES = (31, 32, 63, 64, 127, 128, 191, 192, 255, 256, 319, 320, 351, 352)


@dataclass(frozen=True)
class State:
    point: tuple[int, int] | None
    a: int
    b: int
    trail: int
    steps: int
    active: int = 1


def integer(value):
    return value.to_bytes(WIDTH, "little")


def encode_state(s):
    x, y = (0, 0) if s.point is None else s.point
    return b"".join(map(integer, (x, y, s.a, s.b))) + struct.pack(
        "<QQII", s.trail, s.steps, s.active, int(s.point is None))


def initial_states(c):
    pairs = [(0, 0), (1, 0), (0, 1), (c.n-1, 0),
             (0, c.n-1), (1, 1), (c.n-1, c.n-1)]
    pairs += [((1 << k)-1, 1 << k) for k in BOUNDARIES if (1 << k) < c.n]
    result = []
    for i, (a, b) in enumerate(pairs):
        p = c.mul2(a, b)
        result.append(State(p, a, b, (1 << 63) + i, (1 << 40) + 17*i))
    # The engine must preserve an inactive lane byte for byte.
    result.append(State(c.G, 1, 0, (1 << 64)-1, (1 << 48)-1, 0))
    return result


def jump_table(c, unit):
    if unit:
        return [(c.G, 1, 0)] * 32
    result = []
    for i in range(32):
        a, b = (i+1) % c.n, (2*i+3) % c.n
        p = c.mul2(a, b)
        if p is None:
            a = (a+1) % c.n
            p = c.mul2(a, b)
        assert p is not None
        result.append((p, a, b))
    return result


def job(c, states, table, dp_bits, count):
    header = b"ECCJOB03" + struct.pack(
        "<8I", int(c.field_kind == "binary"), c.bits, c.n.bit_length(),
        dp_bits, len(states), count, 32, 0)
    constants = b"".join(map(integer, (
        c.modulus, c.a, c.b, c.n, *c.G, *c.Q)))
    jumps = b"".join(b"".join(map(integer, (*p, a, b)))
                     for p, a, b in table)
    result = header + constants + jumps
    assert len(result) == 6568
    return result


def reference(c, original, table, dp_bits, count):
    states, dps = [], []
    partition_indices = set()
    for s in original:
        current = s
        if current.active:
            for _ in range(count):
                assert current.steps < (1 << 64)-1
                digest = None if current.point is None else hashlib.sha256(b"".join(v.to_bytes(c.field_bytes, "big") for v in current.point)).digest()
                index = 0 if digest is None else digest[0] & 31
                partition_indices.add(index)
                point, da, db = table[index]
                p = c.add(current.point, point)
                current = replace(current, point=p, a=(current.a+da) % c.n,
                                  b=(current.b+db) % c.n, steps=current.steps+1)
                if p is not None and not (int.from_bytes(hashlib.sha256(b"".join(v.to_bytes(c.field_bytes, "big") for v in p)).digest(), "big") & ((1 << dp_bits)-1)):
                    current = replace(current, active=0)
                    dps.append(current)
                    break
        assert c.on_curve(current.point)
        # This independently checks the coefficient invariant at the endpoint.
        assert c.mul2(current.a, current.b) == current.point
        states.append(current)
    return states, dps, sorted(partition_indices)


def invoke(binary, backend, folder, job_bytes, input_bytes, expect_success=True):
    folder.mkdir()
    jp, ip, op, dp = [folder / n for n in ("job.bin", "in.bin", "out.bin", "dp.bin")]
    jp.write_bytes(job_bytes)
    ip.write_bytes(input_bytes)
    cmd = [str(binary), "--backend", backend, str(jp), str(ip), str(op), str(dp)]
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    (folder / "stdout.txt").write_bytes(completed.stdout)
    (folder / "stderr.txt").write_bytes(completed.stderr)
    (folder / "command.json").write_text(json.dumps(cmd))
    if expect_success:
        assert completed.returncode == 0, (
            folder, completed.stderr.decode(errors="replace"))
        return op.read_bytes(), dp.read_bytes()
    assert completed.returncode != 0, folder
    assert not op.exists() and not dp.exists(), folder
    return None


def main(argv=None):
    if not __debug__:
        raise RuntimeError("correctness checks require Python without optimization")
    p = argparse.ArgumentParser()
    p.add_argument("--cpu", type=Path, required=True)
    p.add_argument("--cuda", type=Path)
    p.add_argument("--metal", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    engines = [("cpu", args.cpu.resolve())]
    if args.cuda:
        engines.append(("cuda", args.cuda.resolve()))
    if args.metal:
        engines.append(("metal", args.metal.resolve()))
    engine_hashes = {backend: hashlib.sha256(binary.read_bytes()).hexdigest()
                     for backend, binary in engines}
    instances = all_instances()
    assert len(instances) == 24
    report = []
    for campaign_number, c in enumerate(instances, 1):
        root = args.out / c.id
        root.mkdir()
        start = initial_states(c)
        for label, dp_bits, steps, unit in (
            ("mixed-dp1", 1, 17, False),
            ("mixed-dp40", 40, 17, False),
            ("unit-one-step", 40, 1, True),
        ):
            table = jump_table(c, unit)
            jb = job(c, start, table, dp_bits, steps)
            ib = b"".join(map(encode_state, start))
            expected, expected_dps, indices = reference(
                c, start, table, dp_bits, steps)
            if dp_bits == 1:
                assert expected_dps, (c.id, "no hash-predicate DP in correctness controls")
            expected_bytes = b"".join(map(encode_state, expected))
            expected_dp_bytes = b"".join(map(encode_state, expected_dps))
            # -G followed by the unit jump must encode canonical infinity.
            if unit:
                assert expected[3].point is None
                assert encode_state(expected[3])[:96] == bytes(96)
                assert expected[3].a == 0 and expected[3].b == 0
            for backend, binary in engines:
                out, dps = invoke(
                    binary, backend, root / (label+"-"+backend), jb, ib)
                assert out == expected_bytes, (
                    c.id, label, backend, "state mismatch")
                assert dps == expected_dp_bytes, (
                    c.id, label, backend, "DP mismatch")
            report.append({
                "campaign": c.id,
                "instance_sha256": c.instance_sha256,
                "case": label,
                "field_bits": c.bits,
                "scalar_bits": c.n.bit_length(),
                "states": len(start),
                "steps_per_round": steps,
                "partition_indices": indices,
                "expected_dps": len(expected_dps),
                "backends_checked": [x[0] for x in engines],
                "input_sha256": hashlib.sha256(jb+ib).hexdigest(),
                "state_sha256": hashlib.sha256(expected_bytes).hexdigest(),
                "dp_sha256": hashlib.sha256(expected_dp_bytes).hexdigest(),
            })
        table = jump_table(c, True)
        good = State(c.G, 1, 0, 7, 0)
        jb = job(c, [good], table, 40, 1)
        good_bytes = encode_state(good)
        # Reject noncanonical infinity rather than silently normalizing it.
        malformed = bytearray(good_bytes)
        struct.pack_into("<I", malformed, 212, 1)
        # A coordinate p (prime) or 2^m (binary) is noncanonical.
        bad_field = c.modulus if c.field_kind == "prime" else 1 << c.bits
        malformed_field = integer(bad_field) + good_bytes[48:]
        malformed_scalar = good_bytes[:96] + integer(c.n) + good_bytes[144:]
        exhausted = encode_state(replace(good, steps=(1 << 64)-1))
        for name, payload in (
            ("bad-infinity", bytes(malformed)),
            ("bad-field", malformed_field),
            ("bad-scalar", malformed_scalar),
            ("step-overflow", exhausted),
        ):
            for backend, binary in engines:
                invoke(binary, backend, root / (name+"-"+backend),
                       jb, payload, False)
        receipt = {
            "format": "native-correctness-v1",
            "all_24_campaigns": campaign_number == 24,
            "campaigns_completed": campaign_number,
            "cuda_checked": args.cuda is not None,
            "metal_checked": args.metal is not None,
            "backends_checked": [backend for backend, _ in engines],
            "engine_sha256": engine_hashes,
            "checks": campaign_number * 7 * len(engines),
            "rows": report,
        }
        # Preserve completed campaign receipts if a later campaign fails.
        (args.out / "receipt.json").write_text(
            json.dumps(receipt, indent=2)+"\n")
    print(json.dumps({
        "campaigns_checked": len(instances),
        "cases": len(report),
        "checks": len(instances) * 7 * len(engines),
        "backends_checked": [backend for backend, _ in engines],
        "cuda_checked": args.cuda is not None,
        "metal_checked": args.metal is not None,
        "receipt": str(args.out / "receipt.json"),
    }))


def worker_main():
    """Check the installed engines without a key, gateway or worker state."""
    os.environ.pop("ECC_API_KEY", None)
    from .client import engine_for_backend, probe_engine, select_backend

    backend = select_backend()
    engine = engine_for_backend(backend)
    probe = probe_engine(engine)
    if backend not in probe.get("backends", ()):
        raise RuntimeError(f"requested {backend} backend is unavailable in {engine}")
    if backend == "cuda" and not probe.get("cuda_devices"):
        raise RuntimeError("CUDA check requires a visible NVIDIA GPU and compatible driver")
    if backend == "metal" and not probe.get("metal_devices"):
        raise RuntimeError("Metal check requires a visible Metal device")
    arguments = ["--cpu", str(engine_for_backend("cpu"))]
    if backend != "cpu":
        arguments += ["--" + backend, str(engine)]
    destination = os.environ.get("ECC_CHECK_OUT")
    if destination:
        main(arguments + ["--out", destination])
    else:
        with tempfile.TemporaryDirectory(prefix="ecc-check-") as temporary:
            main(arguments + ["--out", str(Path(temporary) / "controls")])
    return 0


if __name__ == "__main__":
    main()
