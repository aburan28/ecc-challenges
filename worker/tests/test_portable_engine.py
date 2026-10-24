"""Engine correctness on this host, and Metal parity with the CPU backend.

The CPU backend is checked against an independent pure-Python walk on every
Certicom instance. The Metal backend, where the engine has it and a device is
present, must reproduce the CPU backend's lane states and distinguished points
exactly on every instance, across chunk boundaries and partial threadgroups.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from ecc_challenges.multicurve.codec import WALK_V2, Namespace, Record
from ecc_challenges.multicurve.instances import instance_ids
from generic.runtime import NativeState, run_round

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "18c3053b-96df-4879-a49d-18a52b8fdf8b"


def reference_round(ns, states, steps):
    table = ns.table()
    output = []
    for initial in states:
        point, a, b = initial.point, initial.a, initial.b
        count, active = initial.steps, initial.active
        if active:
            for _ in range(steps):
                delta, da, db = table[ns.partition(point)]
                point = ns.instance.add(point, delta)
                a, b = (a + da) % ns.instance.n, (b + db) % ns.instance.n
                count += 1
                if ns.distinguished(point):
                    active = 0
                    break
        output.append(NativeState(Record(initial.trail_id, count, point, a, b), active))
    return tuple(output)


def has_metal_device(probe):
    return "metal" in probe.get("backends", ()) and bool(probe.get("metal_devices"))


def test_self_test_and_probe(engine, probe):
    result = subprocess.run([str(engine), "--self-test"], check=True,
                            capture_output=True, text=True, timeout=120)
    assert json.loads(result.stdout)["self_test"] == "passed"
    assert probe["backends"][0] == "cpu"
    assert probe["cpu_threads"] >= 1


def test_engine_compiles_with_windows_rpc_small_macro(tmp_path):
    """The Windows SDK's RPC macro must not replace the EC small() helper."""
    compiler = shutil.which("clang++") or shutil.which("g++")
    if compiler is None:
        pytest.skip("a GCC-compatible C++ compiler is unavailable")
    source = tmp_path / "windows_rpc_macro.cpp"
    source.write_text('#define small char\n#include "engine.cu"\n')
    compiled = subprocess.run(
        [compiler, "-std=c++17", "-fsyntax-only", "-I",
         str(ROOT / "worker/generic"), str(source)],
        capture_output=True, text=True, timeout=120)
    assert compiled.returncode == 0, compiled.stderr


@pytest.mark.parametrize("campaign", instance_ids())
def test_cpu_walk_matches_python_reference(engine, campaign):
    # The reference replays the published walk; the 131-bit curves default to a newer one.
    ns = Namespace(campaign, dp_bits=3, walk_version=WALK_V2)
    initial = tuple(NativeState(ns.start(RUN_ID, trail)) for trail in range(3))
    result = run_round(engine, "cpu", ns, initial, 12, timeout=300)
    assert result.states == reference_round(ns, initial, 12)


@pytest.mark.parametrize("campaign", instance_ids())
def test_metal_matches_cpu(engine, probe, campaign):
    if not has_metal_device(probe):
        pytest.skip("engine has no Metal backend or no Metal device is present")
    ns = Namespace(campaign, dp_bits=4, walk_version=WALK_V2)
    # 37 lanes leave a partial threadgroup; 40 steps cross two 16-step chunks.
    initial = tuple(NativeState(ns.start(RUN_ID, trail)) for trail in range(37))
    cpu = run_round(engine, "cpu", ns, initial, 40, timeout=600)
    metal = run_round(engine, "metal", ns, initial, 40, timeout=600)
    assert metal.states == cpu.states
    assert metal.distinguished == cpu.distinguished
    assert metal.receipt["backend"] == "metal"


@pytest.mark.parametrize("campaign", ("ecc2-131", "eccp-131"))
@pytest.mark.parametrize("walk", (2, 3, 4))
def test_fast_metal_matches_cpu(engine, probe, campaign, walk):
    """The specialised GPU kernels reproduce the specialised CPU engine."""
    if not has_metal_device(probe):
        pytest.skip("engine has no Metal backend or no Metal device is present")
    ns = Namespace(campaign, dp_bits=4, walk_version=walk)
    # 10 lanes: a partial last thread (4 lanes per thread); 100 steps: two chunks.
    initial = tuple(NativeState(ns.start(RUN_ID, trail)) for trail in range(10))
    cpu = run_round(engine, "cpu", ns, initial, 100, timeout=600)
    metal = run_round(engine, "metal", ns, initial, 100, timeout=600)
    assert metal.receipt["engine"] == "fast131"
    assert metal.states == cpu.states
    assert metal.distinguished == cpu.distinguished


def test_prime_negation_metal_is_repeatable(engine, probe):
    """A prime walk-4 lane stays valid across repeated Metal command buffers."""
    if not has_metal_device(probe):
        pytest.skip("engine has no Metal backend or no Metal device is present")
    ns = Namespace("eccp-131", dp_bits=4, walk_version=4)
    initial = tuple(NativeState(ns.start(RUN_ID, trail)) for trail in range(10))
    expected = run_round(engine, "cpu", ns, initial, 100, timeout=600)
    for _ in range(4):
        actual = run_round(engine, "metal", ns, initial, 100, timeout=600)
        assert actual.receipt["engine"] == "fast131"
        assert actual.receipt["metal_soft_mul32"] == any(
            "Paravirtual" in name for name in probe["metal_devices"])
        assert actual.states == expected.states
        assert actual.distinguished == expected.distinguished


def test_metal_resumed_rounds_match_one_round(engine, probe):
    if not has_metal_device(probe):
        pytest.skip("engine has no Metal backend or no Metal device is present")
    ns = Namespace("eccp-79", dp_bits=2)
    initial = tuple(NativeState(ns.start(RUN_ID, trail)) for trail in range(5))
    single = run_round(engine, "metal", ns, initial, 23, timeout=300)
    resumed = initial
    for steps in (5, 7, 11):
        resumed = run_round(engine, "metal", ns, resumed, steps, timeout=300).states
    assert resumed == single.states == reference_round(ns, initial, 23)


def test_backend_missing_from_build_is_refused(engine, probe):
    missing = next(b for b in ("cuda", "metal") if b not in probe["backends"])
    ns = Namespace("eccp-79", dp_bits=4)
    with pytest.raises(RuntimeError, match="unavailable in this build"):
        run_round(engine, missing, ns, [NativeState(ns.start(RUN_ID, 0))], 1)


def kernel_source(name):
    text = (ROOT / "worker/generic/engine_metal.mm").read_text()
    return re.search(r'const char ' + name + r'\[\] = R"ECCMSL\((.*?)\)ECCMSL"',
                     text, re.S).group(1)


@pytest.mark.parametrize("fast,soft", ((False, False), (True, False), (True, True)),
                         ids=("reference", "fast131", "fast131-soft32"))
def test_metal_kernel_respects_address_spaces(tmp_path, fast, soft):
    """Type-check the Metal configuration of ec_core.h and the real kernel.

    Clang's C++ for OpenCL enforces address spaces on references the way the
    Metal compiler does, so this runs on any host with clang, without a Mac.
    """
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("clang is unavailable")
    probe_source = tmp_path / "probe.clcpp"
    # Some clang builds cannot copy a struct in this mode at all (even
    # returning a local), which says nothing about the Metal compiler.
    probe_source.write_text(
        "struct S { unsigned w[2]; };\n"
        "S copy(S s) { S r = s; return r; }\n"
        "S load(const __constant S& c) { S r{}; r.w[0] = c.w[0]; return copy(r); }\n")
    # SPIR uses OpenCL's generic address space for implicit copy operations;
    # targeting a host CPU can reject even the probe's valid struct copies.
    target = ["-target", "spir64-unknown-unknown"]
    if subprocess.run([clang, *target, "-x", "clcpp", "-cl-std=clc++2021", "-fsyntax-only",
                       str(probe_source)], capture_output=True).returncode:
        pytest.skip("this clang's C++ for OpenCL 2021 cannot copy structs")
    # OpenCL has no Metal attributes or kernels taking references, so the
    # kernel body is checked as an ordinary function with the same signature.
    kernel = re.sub(r"\[\[[^\]]*\]\]", "",
                    kernel_source("fast_kernel_source" if fast else "kernel_source"))
    kernel = kernel.replace("kernel void", "void")
    fast_header = (
        ('#define F131_SOFT_MUL32 1\n' if soft else '') +
        '#include "fast131.h"\n#define FAST_K 4\n' if fast else "")
    source = tmp_path / "metal_emulation.clcpp"
    source.write_text(
        "#define __METAL_VERSION__ 300\n#define thread __private\n"
        "#define constant __constant\n#define device __global\n"
        "#undef local\n#define local lane_storage\n"
        '#include "ec_core.h"\n' + fast_header + kernel)
    checked = subprocess.run(
        [clang, *target, "-x", "clcpp", "-cl-std=clc++2021", "-fsyntax-only", "-Werror",
         "-I", str(ROOT / "worker/generic"), str(source)],
        capture_output=True, text=True, timeout=120)
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize("option", ("--emit-metal-source", "--emit-metal-fast-source",
                                    "--emit-metal-soft32-source"))
def test_emitted_metal_source_compiles(engine, probe, tmp_path, option):
    """On a Mac with the Metal toolchain, compile the exact embedded shader."""
    if "metal" not in probe.get("backends", ()):
        pytest.skip("engine has no Metal backend")
    if shutil.which("xcrun") is None or subprocess.run(
            ["xcrun", "-sdk", "macosx", "--find", "metal"],
            capture_output=True).returncode:
        pytest.skip("the offline Metal compiler is not installed")
    source = tmp_path / "walk.metal"
    source.write_text(subprocess.run(
        [str(engine), option], check=True, capture_output=True,
        text=True, timeout=60).stdout)
    compiled = subprocess.run(
        ["xcrun", "-sdk", "macosx", "metal", "-c", str(source),
         "-o", str(tmp_path / "walk.air")],
        capture_output=True, text=True, timeout=300, env=dict(os.environ))
    if compiled.returncode and "Metal Toolchain" in compiled.stderr:
        pytest.skip("the Metal toolchain component is not installed")
    assert compiled.returncode == 0, compiled.stderr
