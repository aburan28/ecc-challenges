"""CPU engine parity tests across every exact Certicom challenge instance."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import shutil
import struct
import subprocess
import sys

import pytest

from ecc_challenges.multicurve.codec import Namespace, Record
from ecc_challenges.multicurve.instances import (
    instance_ids,
    registry_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ecc_generic_runtime",
    ROOT / "worker/generic/runtime.py",
)
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


@pytest.fixture(scope="session")
def cpu_engine(tmp_path_factory):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.fail(
            "a C++17 compiler is required "
            "for the native engine correctness gate"
        )
    executable = (
        tmp_path_factory.mktemp("ecc-native") / "ecc-engine-cpu"
    )
    subprocess.run(
        [
            compiler, "-std=c++17", "-O2", "-x", "c++",
            str(ROOT / "worker/generic/engine.cu"),
            "-o", str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    checked = subprocess.run(
        [str(executable), "--self-test"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert json.loads(checked.stdout)["self_test"] == "passed"
    return executable


class UnitJumpNamespace(Namespace):
    """A controlled G jump isolates native arithmetic and scalar carries.

    These controls intentionally do not stand in for the production walk table;
    separate tests below exercise the real namespace jumps and first-DP stop.
    """

    def table(self):
        return ((self.instance.G, 1, 0),) * 32


def coefficient_cases(n):
    cases = [
        (0, 0),
        (1, 0),
        (0, 1),
        (n - 1, 0),
        (n - 1, n - 1),
    ]
    for bit in (
        31, 32, 63, 64, 95, 96, 127, 128, 159, 160,
        191, 192, 223, 224, 255, 256, 287, 288,
        319, 320, 351, 352,
    ):
        value = 1 << bit
        if value < n:
            cases.append((value - 1, value))
    return tuple(dict.fromkeys(cases))


@pytest.mark.parametrize("campaign", instance_ids())
def test_all24_native_cpu_wide_arithmetic(cpu_engine, campaign):
    ns = UnitJumpNamespace(campaign, dp_bits=40)
    instance = ns.instance
    cases = list(coefficient_cases(instance.n))
    negative_g = instance.neg(instance.G)
    sum_gq = instance.add(instance.G, instance.Q)
    points = {
        (0, 0): None,
        (1, 0): instance.G,
        (0, 1): instance.Q,
        (instance.n - 1, 0): negative_g,
        (instance.n - 1, instance.n - 1): instance.neg(sum_gq),
    }
    # Reuse an independently doubled G+Q ladder to avoid recomputing a full
    # scalar product for every boundary vector.
    wanted = {
        b.bit_length() - 1
        for a, b in cases
        if (a, b) not in points
    }
    power = sum_gq
    for bit in range(max(wanted, default=-1) + 1):
        if bit in wanted:
            value = 1 << bit
            points[value - 1, value] = instance.add(
                power, negative_g
            )
        power = instance.add(power, power)
    vector = registry_metadata()["reference_vectors"].get(campaign)
    if vector:
        published = (int(vector["scalar"], 16), 0)
        cases.append(published)
        points[published] = instance.Q
    states = [
        runtime.NativeState(
            Record(trail, 11, points[a, b], a, b), 1
        )
        for trail, (a, b) in enumerate(cases)
    ]
    # An inactive lane must remain byte-for-byte unchanged and must not
    # appear again in the DP stream.
    states.append(
        runtime.NativeState(
            Record(10000, 17, instance.G, 1, 0), 0
        )
    )
    result = runtime.run_round(
        cpu_engine, "cpu", ns, states, 1, timeout=300
    )
    expected = []
    for state in states:
        if not state.active:
            expected.append(state)
            continue
        point = instance.add(state.point, instance.G)
        active = int(
            not ns.distinguished(point)
        )
        record = Record(
            state.trail_id,
            state.steps + 1,
            point,
            (state.a + 1) % instance.n,
            state.b,
        )
        expected.append(runtime.NativeState(record, active))
    assert result.states == tuple(expected)
    assert result.receipt["lanes"] == len(states)
    assert result.distinguished == tuple(
        after.record
        for before, after in zip(states, expected)
        if before.active and not after.active
    )
    # Identity -> G and -G -> identity, with n-1 + 1 -> 0.
    assert result.states[0].point == instance.G
    assert result.states[3].point is None
    assert result.states[3].a == 0


def reference_round(ns, states, steps):
    output = []
    table = ns.table()
    for initial in states:
        point = initial.point
        a, b = initial.a, initial.b
        count, active = initial.steps, initial.active
        if active:
            for _ in range(steps):
                index = ns.partition(point)
                delta, da, db = table[index]
                point = ns.instance.add(point, delta)
                a = (a + da) % ns.instance.n
                b = (b + db) % ns.instance.n
                count += 1
                if (
                    ns.distinguished(point)
                ):
                    active = 0
                    break
        output.append(
            runtime.NativeState(
                Record(initial.trail_id, count, point, a, b),
                active,
            )
        )
    return tuple(output)


@pytest.mark.parametrize("campaign", ("eccp-79", "ecc2-79"))
def test_real_walk_first_dp_and_round_resume(cpu_engine, campaign):
    ns = Namespace(campaign, dp_bits=2)
    run_id = "18c3053b-96df-4879-a49d-18a52b8fdf8b"
    initial = tuple(
        runtime.NativeState(ns.start(run_id, trail))
        for trail in range(3)
    )
    expected = reference_round(ns, initial, 23)
    single = runtime.run_round(
        cpu_engine, "cpu", ns, initial, 23, timeout=300
    )
    assert single.states == expected
    resumed = initial
    emitted = []
    for steps in (5, 7, 11):
        result = runtime.run_round(
            cpu_engine, "cpu", ns, resumed, steps, timeout=300
        )
        resumed = result.states
        emitted.extend(result.distinguished)
    assert resumed == single.states
    assert sorted(
        emitted, key=lambda record: record.trail_id
    ) == sorted(
        single.distinguished,
        key=lambda record: record.trail_id,
    )
    for record in emitted:
        ns.verify(record)


def test_native_serialization_exact_width_and_endianness():
    maximum = (1 << 384) - 1
    record = Record(
        (1 << 64) - 1,
        (1 << 64) - 1,
        (1 << 383, 7),
        maximum,
        1 << 320,
    )
    encoded = runtime.pack_state(record, active=0)
    assert len(encoded) == 216
    assert encoded[47] == 0x80
    assert int.from_bytes(encoded[96:144], "little") == maximum
    assert struct.unpack_from("<QQII", encoded, 192) == (
        (1 << 64) - 1,
        (1 << 64) - 1,
        0,
        0,
    )
    decoded, = runtime.unpack_states(encoded)
    assert decoded == runtime.NativeState(record, 0)
    identity = Record(0, 0, None, 0, 0)
    assert (
        runtime.unpack_states(runtime.pack_state(identity))[0].point
        is None
    )
    assert runtime.unpack_states(b"") == []
    with pytest.raises(ValueError, match="truncated"):
        runtime.unpack_states(encoded[:-1])
    malformed = bytearray(encoded)
    struct.pack_into("<I", malformed, 208, 2)
    with pytest.raises(ValueError, match="active"):
        runtime.unpack_states(malformed)
    malformed = bytearray(encoded)
    struct.pack_into("<I", malformed, 212, 1)
    with pytest.raises(ValueError, match="infinity"):
        runtime.unpack_states(malformed)


def test_native_job_geometry_and_limits():
    ns = UnitJumpNamespace("eccp-359", dp_bits=40)
    encoded = runtime.pack_job(ns, 3, 7)
    assert len(encoded) == 6568
    assert struct.unpack_from("<8s8I", encoded) == (
        b"ECCJOB03",
        0,
        ns.instance.bits,
        ns.instance.n.bit_length(),
        40,
        3,
        7,
        32,
        0,
    )
    assert (
        int.from_bytes(encoded[40:88], "little")
        == ns.instance.modulus
    )
    for lanes, steps in (
        (0, 1),
        (4097, 1),
        (1, 0),
        (1, (1 << 20) + 1),
    ):
        with pytest.raises(ValueError):
            runtime.pack_job(ns, lanes, steps)
