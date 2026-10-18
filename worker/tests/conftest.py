"""Worker tests that run on every supported OS and architecture.

They need only Python, pytest and a built engine: no PostgreSQL, AWS mocks or
service dependencies, so the same suite runs on Linux, macOS and Windows,
x86-64 and arm64. Set ECC_TEST_ENGINE to test a prebuilt engine (a CMake or
installer build, or a Metal build); otherwise one is compiled with g++/clang++.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
sys.path.append(str(ROOT / "src"))


@pytest.fixture(scope="session")
def engine(tmp_path_factory) -> Path:
    supplied = os.environ.get("ECC_TEST_ENGINE")
    if supplied:
        return Path(supplied)
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.fail("set ECC_TEST_ENGINE or install a C++17 compiler")
    executable = tmp_path_factory.mktemp("engine") / "generic-engine"
    subprocess.run(
        [compiler, "-std=c++17", "-O2", "-x", "c++",
         str(ROOT / "worker/generic/engine.cu"), "-o", str(executable)],
        check=True, capture_output=True, text=True, timeout=300,
    )
    return executable


@pytest.fixture(scope="session")
def probe(engine) -> dict:
    result = subprocess.run([str(engine), "--probe"], check=True,
                            capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)
