# Shared-core `xtime` integration check, 2026-10-19

The `main` worker now keeps binary-field arithmetic in `worker/generic/ec_core.h`,
which CPU, CUDA, and Metal share. The original 2026-10-16 receipts in the parent
directory bind the earlier standalone `engine.cu` implementation. They are
historical controls; their GPU receipt does not validate this adapted source.

The initial candidate applied a bounded-word shift and polynomial fold to the
shared header. `baseline-ec_core.h` in the parent directory is the exact
`origin/main` header at the baseline commit named in `receipt.json`. Both CPU
arms compiled the same current `engine.cu`; only their headers differed. The
engine self-test compares `binary_xtime` with a full-width shift and fold for
four operand patterns at each degree from 2 through 359. The candidate was
removed from the deliverable after the matched L4 results below; the reference
self-test remains.

The focused portable-engine suite completed with 26 passed and 27 platform
or toolchain skips (`PYTHONPATH=src python3 -m pytest -q
worker/tests/test_portable_engine.py`).

The local CMake macOS Metal-wrapper build succeeded with CUDA and OpenMP
disabled. `ctest` passed both the engine self-test and probe. The probe
reported no accessible Metal device, so this did not execute the shader.

The native finite-control program completed all 24 pinned campaigns on each
CPU arm: 72 positive cases and 96 malformed-input rejection cases per arm.
Every positive case independently checked state and distinguished-point bytes
against the Python curve reference. The paired arms produced identical raw
state and distinguished-point bytes in all 72 cases. `receipt.json` records
source and binary hashes, compiler, commands, environment, and pairwise hashes.
The two compressed archives retain all inputs, outputs, commands, stdout,
stderr, and each program's own receipt.

The local Metal address-space test skipped because the installed Clang cannot
copy structs in its OpenCL C++ mode; the offline Metal compiler is unavailable.
The subsequent L4 runs are recorded in `l4-insight-20261019.md` with raw
artifacts for all three attempts. Their matched outputs passed, but the
bounded-word candidate was slower on the larger panel. CI and published-image
checks still need to run on the final branch revision before release.
