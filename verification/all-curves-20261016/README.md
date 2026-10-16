# Portable engine correctness controls, 2026-10-16

Current generic format: ECCJOB03, with SHA-256 point partitioning and DP selection. `cpu-job03.json` records72 passing cases across all24 curves, each with nonempty DP1 output. `job03-controls.tar.gz` preserves all exact inputs and expected outputs. `source-job03-sha256.json` binds the corrected implementation. Its GPU rerun and final container canary remain release gates.

The earlier `cpu.json`, `cuda-l4.json` and `source-sha256.json` below are superseded raw-coordinate predicate receipts. Their exact sources are retained under `raw-x-v1/`; those receipts establish arithmetic parity only for the earlier proposal.

`cpu.json` records 72 finite transition cases over all 24 published Certicom
instances. `cuda-l4.json` records 168 successful comparisons/rejections on an
NVIDIA L4 through Modal app `ap-O0t5St6b2trML1ZEAZWDSm`, driver610.57.04.
All successful CUDA outputs matched the independently checked CPU outputs byte
for byte. Invalid infinity, coordinate, coefficient and exhausted step-counter
controls were rejected. `source-sha256.json` binds the actual sources and exact
parameter registry used for those controls. These are correctness receipts;
they contain no throughput estimate or solution-status conclusion.

CPU native arithmetic was compared with the independent Python affine group
implementation, including scalar limb boundaries through352 bits, infinity
transitions, changing target coefficients, modular wrap and DP termination.
The parameter registry separately passed all24 on-curve/order/field checks and
all10 published scalar correctness vectors. Prime tests are probable-prime
checks, not formal primality certificates.

Reproduce finite controls:

```sh
c++ -x c++ -std=c++17 -O2 worker/generic/engine.cu -o /tmp/ecc-engine
/tmp/ecc-engine --self-test
PYTHONPATH=src python scripts/check_native_curves.py --cpu /tmp/ecc-engine --out /path/to/new-controls
ECC_NATIVE_CONTROLS=/path/to/new-controls modal run scripts/check_modal_curves.py
```

Each control retains its job, input states, output states, emitted points,
command, stdout and stderr under the selected output directory. The original
local run is retained at `/private/tmp/ecc-native-controls-20261016a`.
The final combined contributor image and public service admission/collision
canary require separate release evidence. Compiling this source on Modal does
not verify a different image's entrypoint or driver requirements.

Two earlier Modal wrapper attempts failed before correctness execution: the
first omitted Modal Python image metadata; the second read a client-only
environment variable during remote import. Their app IDs are
`ap-PYwUGQP0IFbMZGZByLY5ui` and `ap-JxOwG4fngXpZZkGYNxTg1i`.
The source compiled and its CPU self-test passed in both builds. These failures
are retained as infrastructure outcomes; neither supplied arithmetic evidence.
