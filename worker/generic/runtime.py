"""Explicit binary I/O and one-round execution for the portable EC engine.

Engine files use 384-bit little-endian integers. Public DP uploads continue to
use the namespace's separate fixed-width affine witness codec.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import struct
import subprocess
import tempfile
from typing import Sequence

from ecc_challenges.multicurve.codec import MAX_TRAIL_STEPS, Namespace, Record

INTEGER_BYTES = 48
JOB_BYTES = 6568
STATE_BYTES = 216
MAX_LANES = 4096
MAX_ROUND_STEPS = 1 << 20
JOB_HEADER = struct.Struct("<8s8I")
STATE_TAIL = struct.Struct("<QQII")


def _unsigned(value: int, bits: int, name: str) -> int:
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f"{name} must be an unsigned {bits}-bit integer")
    return value


def _integer(value: int, name: str) -> bytes:
    return _unsigned(value, 384, name).to_bytes(INTEGER_BYTES, "little")


def _flag(value: int, name: str) -> int:
    if type(value) not in (int, bool) or value not in (0, 1):
        raise ValueError(f"{name} must be zero or one")
    return int(value)


@dataclass(frozen=True)
class NativeState:
    record: Record
    active: int = 1

    @property
    def trail_id(self):
        return self.record.trail_id

    @property
    def steps(self):
        return self.record.steps

    @property
    def point(self):
        return self.record.point

    @property
    def a(self):
        return self.record.a

    @property
    def b(self):
        return self.record.b

    @property
    def infinity(self):
        return self.point is None


@dataclass(frozen=True)
class RoundResult:
    states: tuple[NativeState, ...]
    distinguished: tuple[Record, ...]
    receipt: dict


def pack_job(ns: Namespace, lanes: int, steps: int) -> bytes:
    """Encode the exact ECCJOB03 format consumed by engine.cu."""
    _unsigned(lanes, 32, "lanes")
    _unsigned(steps, 32, "steps")
    if not 1 <= lanes <= MAX_LANES or not 1 <= steps <= MAX_ROUND_STEPS:
        raise ValueError("native job exceeds its lane or round-step bounds")
    instance = ns.instance
    if instance.field_kind not in ("prime", "binary"):
        raise ValueError("unsupported field kind")
    if not 2 <= instance.bits <= 359 or not 2 <= instance.n.bit_length() <= 360:
        raise ValueError("instance exceeds the native engine width")
    if type(ns.dp_bits) is not int or not 1 <= ns.dp_bits <= 40:
        raise ValueError("DP bits must be between one and forty")
    encoded = bytearray(JOB_HEADER.pack(
        b"ECCJOB03", int(instance.field_kind == "binary"), instance.bits,
        instance.n.bit_length(), ns.dp_bits, lanes, steps, 32, 0,
    ))
    for name, value in (
        ("modulus", instance.modulus), ("a", instance.a), ("b", instance.b),
        ("n", instance.n), ("G.x", instance.G[0]), ("G.y", instance.G[1]),
        ("Q.x", instance.Q[0]), ("Q.y", instance.Q[1]),
    ):
        encoded.extend(_integer(value, name))
    table = ns.table()
    if len(table) != 32:
        raise ValueError("the native engine requires exactly 32 jumps")
    for index, (point, da, db) in enumerate(table):
        if point is None or not instance.on_curve(point):
            raise ValueError(f"jump {index} must be a finite canonical curve point")
        if type(da) is not int or type(db) is not int or not (
            0 <= da < instance.n and 0 <= db < instance.n
        ):
            raise ValueError(f"jump {index} has noncanonical coefficients")
        for name, value in (
            ("x", point[0]), ("y", point[1]), ("da", da), ("db", db)
        ):
            encoded.extend(_integer(value, f"jump {index}.{name}"))
    if len(encoded) != JOB_BYTES:
        raise RuntimeError("native job encoder produced an unexpected length")
    return bytes(encoded)


def pack_state(record: Record, active: int = 1) -> bytes:
    """Encode an intermediate state; point=None uses canonical infinity."""
    active = _flag(active, "active")
    trail = _unsigned(record.trail_id, 64, "trail ID")
    steps = _unsigned(record.steps, 64, "steps")
    infinity = int(record.point is None)
    if infinity:
        x = y = 0
    else:
        if not isinstance(record.point, (tuple, list)) or len(record.point) != 2:
            raise ValueError("a finite point requires exactly two coordinates")
        x, y = record.point
    encoded = b"".join((
        _integer(x, "x"), _integer(y, "y"),
        _integer(record.a, "coefficient a"),
        _integer(record.b, "coefficient b"),
        STATE_TAIL.pack(trail, steps, active, infinity),
    ))
    if len(encoded) != STATE_BYTES:
        raise RuntimeError("native state encoder produced an unexpected length")
    return encoded


def unpack_states(data: bytes) -> list[NativeState]:
    """Decode full 216-byte states, accepting an empty DP output."""
    if len(data) % STATE_BYTES:
        raise ValueError("native state bytes contain a truncated record")
    if len(data) > MAX_LANES * STATE_BYTES:
        raise ValueError("native state bytes exceed the maximum lane count")
    result = []
    for offset in range(0, len(data), STATE_BYTES):
        values = [
            int.from_bytes(
                data[
                    offset + i * INTEGER_BYTES:
                    offset + (i + 1) * INTEGER_BYTES
                ],
                "little",
            )
            for i in range(4)
        ]
        trail, steps, active, infinity = STATE_TAIL.unpack_from(
            data, offset + 4 * INTEGER_BYTES
        )
        _flag(active, "active")
        _flag(infinity, "infinity")
        x, y, a, b = values
        if infinity and (x or y):
            raise ValueError("infinity must contain zero coordinates")
        point = None if infinity else (x, y)
        # Identity is permitted only for an intermediate native state. A public
        # DP record must be finite before Namespace.check_record/verify is used.
        result.append(NativeState(Record(trail, steps, point, a, b), active))
    return result


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise RuntimeError(
            f"native output exceeds its permitted size: {path.name}"
        )
    return data


def _check_state(ns: Namespace, state: NativeState) -> None:
    _flag(state.active, "active")
    record = state.record
    _unsigned(record.trail_id, 64, "trail ID")
    _unsigned(record.steps, 64, "steps")
    if record.steps > MAX_TRAIL_STEPS:
        raise ValueError("state exceeds the namespace trail-step limit")
    if type(record.a) is not int or type(record.b) is not int or not (
        0 <= record.a < ns.instance.n and 0 <= record.b < ns.instance.n
    ):
        raise ValueError("state has noncanonical subgroup coefficients")
    if not ns.instance.on_curve(record.point):
        raise ValueError(
            "state is not a canonical point on the assigned curve"
        )


def run_round(
    engine: str | Path,
    backend: str,
    ns: Namespace,
    states: Sequence[NativeState | Record],
    steps: int,
    directory: str | Path | None = None,
    *,
    timeout: float = 600,
) -> RoundResult:
    """Run one bounded round; accept outputs only after complete validation.

    The caller retains its prior checkpoint until this function returns.
    A requested CUDA failure is reported directly; backend selection belongs
    to the caller.
    """
    if backend not in ("cpu", "cuda"):
        raise ValueError("backend must be cpu or cuda")
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("engine timeout must be positive")
    _unsigned(steps, 32, "steps")
    if not 1 <= steps <= MAX_ROUND_STEPS:
        raise ValueError(
            "native round steps must be between one and 1048576"
        )
    inputs = tuple(
        state if isinstance(state, NativeState) else NativeState(state)
        for state in states
    )
    if not 1 <= len(inputs) <= MAX_LANES:
        raise ValueError("native round requires 1..4096 states")
    for state in inputs:
        _check_state(ns, state)
        if state.active and state.steps + steps > MAX_TRAIL_STEPS:
            raise ValueError(
                "requested round could exceed the trail-step limit"
            )
    if len({state.trail_id for state in inputs}) != len(inputs):
        raise ValueError("each lane must have a distinct trail ID")
    job_bytes = pack_job(ns, len(inputs), steps)
    input_bytes = b"".join(
        pack_state(state.record, state.active) for state in inputs
    )
    with tempfile.TemporaryDirectory(
        prefix="ecc-round-", dir=directory
    ) as temporary:
        root = Path(temporary)
        job, source, output, dps = (
            root / name
            for name in ("job.bin", "input.bin", "output.bin", "dps.bin")
        )
        job.write_bytes(job_bytes)
        source.write_bytes(input_bytes)
        completed = subprocess.run(
            [
                str(engine), "--backend", backend,
                str(job), str(source), str(output), str(dps),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            # Keep Ctrl-C and process-group signals away from the engine: the
            # client catches them, lets this round finish, then saves state.
            start_new_session=True,
        )
        if completed.returncode:
            detail = (
                completed.stderr or completed.stdout
            ).strip()[-2000:]
            raise RuntimeError(
                f"native {backend} round failed "
                f"({completed.returncode}): {detail}"
            )
        try:
            receipt = json.loads(completed.stdout)
        except (ValueError, TypeError) as error:
            raise RuntimeError(
                "native engine returned an invalid receipt"
            ) from error
        output_bytes = _read_bounded(
            output, len(inputs) * STATE_BYTES
        )
        dp_bytes = _read_bounded(
            dps, len(inputs) * STATE_BYTES
        )
    if len(output_bytes) != len(inputs) * STATE_BYTES:
        raise RuntimeError(
            "native output state count differs from input lane count"
        )
    outputs = tuple(unpack_states(output_bytes))
    emitted = tuple(unpack_states(dp_bytes))
    expected_emitted = []
    for before, after in zip(inputs, outputs):
        _check_state(ns, after)
        if after.trail_id != before.trail_id:
            raise RuntimeError(
                "native engine changed a lane's trail identity"
            )
        if before.active == 0:
            if (
                pack_state(before.record, before.active)
                != pack_state(after.record, after.active)
            ):
                raise RuntimeError(
                    "native engine changed an inactive state"
                )
            continue
        if not before.steps < after.steps <= before.steps + steps:
            raise RuntimeError(
                "native engine returned an invalid step increment"
            )
        if after.active:
            if after.steps != before.steps + steps:
                raise RuntimeError(
                    "an active lane did not finish its requested round"
                )
        else:
            if after.point is None:
                raise RuntimeError(
                    "native engine emitted an identity "
                    "as a distinguished point"
                )
            ns.verify(after.record)
            expected_emitted.append(
                pack_state(after.record, after.active)
            )
    expected_bytes = b"".join(expected_emitted)
    if dp_bytes != expected_bytes:
        raise RuntimeError(
            "native DP output differs from "
            "the newly completed lane states"
        )
    if (
        not isinstance(receipt, dict)
        or receipt.get("backend") != backend
        or receipt.get("lanes") != len(inputs)
    ):
        raise RuntimeError(
            "native receipt does not match the requested job"
        )
    if receipt.get("distinguished_points") != len(emitted):
        raise RuntimeError(
            "native receipt has an incorrect distinguished-point count"
        )
    return RoundResult(
        outputs,
        tuple(state.record for state in emitted),
        receipt,
    )


invoke_round = run_round
