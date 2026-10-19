# Shared-core `xtime` integration check, 2026-10-19

The `main` worker now keeps binary-field arithmetic in `worker/generic/ec_core.h`,
which CPU, CUDA, and Metal share. The original 2026-10-16 receipts in the parent
directory bind the earlier standalone `engine.cu` implementation. They are
historical controls; their GPU receipt does not validate this adapted source.

This integration applies the same bounded-word shift and polynomial fold to the
shared header. `baseline-ec_core.h` in the parent directory is the exact
`origin/main` header at the baseline commit named in `receipt.json`. Both CPU
arms compiled the same current `engine.cu`; only their headers differed. The
engine self-test compares the optimized result with a full-width shift and fold
for four operand patterns at each degree from 2 through 359.

The focused portable-engine suite completed with 26 passed and 27 platform
or toolchain skips (`PYTHONPATH=src python3 -m pytest -q
worker/tests/test_portable_engine.py`).

The native finite-control program completed all 24 pinned campaigns on each
CPU arm: 72 positive cases and 96 malformed-input rejection cases per arm.
Every positive case independently checked state and distinguished-point bytes
against the Python curve reference. The paired arms produced identical raw
state and distinguished-point bytes in all 72 cases. `receipt.json` records
source and binary hashes, compiler, commands, environment, and pairwise hashes.
The two compressed archives retain all inputs, outputs, commands, stdout,
stderr, and each program's own receipt.

This is a correctness and source integration check. The local Metal address-space
test skipped because the installed Clang cannot copy structs in its OpenCL C++
mode; the offline Metal compiler is unavailable. This adapted source has not
been run on a GPU, and this check measured no throughput. The prepared matched
L4 launcher compares the same engine with the baseline and candidate shared
headers, records both hashes, and preserves raw workload/output receipts. Its
remote run awaits separate source-export authorization. CI and published-image
checks still need to run on this branch before the change is released.
