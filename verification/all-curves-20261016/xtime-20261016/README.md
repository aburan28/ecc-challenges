# Binary rho `xtime` arithmetic control, 2026-10-16

## Scope and source

This change replaces the ECCJOB03 binary-field `xtime` helper's two
full-width passes with one pass over `ceil((m+1)/32)` limbs. The polynomial
fold uses a mask from the high input bit, so the new path has no data-dependent
branch. It changes neither the ECCJOB03 wire format nor the walk rule. The
source-level limb loop is shorter for fields below 352 bits; no device speed
advantage is asserted from that count alone.

Baseline source: `83b9313bec2f307dd77d35ed0b59c6b6783f1e15b8053e0c703d5708df7de9ef`
at the parent branch head `c3f9e98`. Candidate source:
`c0eaf17e53c272bf7009247ff0d0f938f3cb465480ffae3d00f7b7320244e430`.
Both CPU binaries were built on Apple arm64 with
`c++ -std=c++17 -O3 -x c++ worker/generic/engine.cu -o <binary>`.
The baseline binary SHA-256 is
`866bb200dcd6128adc90aa1385c5b4e502c525c8f65a1b70eb9685e6158d65b0`;
the final candidate binary SHA-256 is
`cf2798ae47de6fca3d49f00a8303e0400b594e640978a7902055d148d97054a3`.

## Correctness

The candidate self-test compares `xtime` with an independent full-width
shift-and-fold reference for four operand patterns at every field degree
from 2 through 359. The native finite-control suite for the final candidate
binary is recorded in `cpu-controls.tar.gz`: all 24 published campaigns,
three positive cases each, plus their malformed-input rejection cases.
The nine binary challenge campaigns also have 36 matched baseline/candidate
invocations in `ab-controls.tar.gz`. Every paired run produced identical
output state bytes, distinguished-point bytes, and actual step counts.

The exact candidate source was also compiled with CUDA 13.0.2 and replayed
on an NVIDIA L4. `cuda-receipt.json` records 168 successful parity or
malformed-input rejection checks across all 24 campaigns. Its source SHA-256
equals the candidate source above. The GPU run used the first candidate CPU
control directory; all 72 positive input, state, and distinguished-point
hash tuples in that directory equal the final candidate CPU control receipt.
Modal run: <https://modal.com/apps/a-buran28/main/ap-2BQOE5FLFCV0ZGf2N1KFNU>.

These finite controls do not attest arbitrary long walks, claimed trail work,
or an end-to-end campaign result. The optimized source still needs a matched
GPU throughput comparison before any speed claim.

## Timing insight

`aa-controls.tar.gz` preserves two nominally identical arms over the same
nine 256-step workloads. The candidate/baseline median wall ratio spans
0.345 to 2.481 even though both arms are the same binary. In the matching
source comparison (`ab-controls.tar.gz`), that ratio spans 0.300 to 2.655.
The host was shared and its CPU isolation was not attested. The A/A result
rejects any CPU wall speed claim from these invocations. Their exact outputs,
commands, timing rows, input hashes, and failures remain in the raw archives.
The benchmark includes process start, validation, and file I/O; it does not
isolate field arithmetic or useful GPU step throughput.

## Reproduction and next gate

Compile both source snapshots with the command above, then run
`scripts/bench_native_binary_rho.py` with `--baseline`, `--candidate`,
`--baseline-source`, `--candidate-source`, and a fresh `--out` directory.
It reads the immutable `job03-controls.tar.gz` inputs and alternates arm
order. Run `scripts/check_native_curves.py` with the candidate CPU binary for
independent state/coefficient controls. The next speed gate is a matched,
same-image L4 baseline/candidate run with exact output equality, useful-step
counts, device telemetry, and an A/A control. A measured gain there would
support a bounded worker-throughput improvement; fleet ingest and recovery
capacity remain separate release gates.
