# Rho131 current-head and nine-curve release checks

This directory separates the base-commit controls from the still-unreleased
Metal repair and the live gates. The base is
`b78fc2921e9b9a60174e1693aff15c7daf23f9c7`, the 2026-10-23 merge of
the specialised 131-bit engine. The live catalog snapshot continues to pin
the earlier worker digest
`sha256:a347a784935a7713b204335e8316f780a5c3209d64cc372980c1b35bee9f81ba`.
PR #55 added a Metal 32-bit multiply variant for Apple Paravirtual devices,
but its hosted macOS x86_64 job repeated the invalid ECCp-131 walk-4 state.
PR #57 tests a 32-bit carry and borrow repair. The key-free L4 control on
its commit `e77c242` passed; its Apple Paravirtual job is pending. No new
image has been advertised.

## Evidence index

All `local/*.log` paths below are members of `local-logs.tar.zst`.
`local-logs.manifest.json` binds the archive digest and each raw log's digest;
the logged output bytes are preserved inside the archive except for three
worktree-path substitutions in one sandbox-denied attempt. The source and
published digests for that log, removed benchmark hostnames, and normalized
archive headers are recorded in `publication-redactions.json` and the log
manifest. The raw source archives remain local and were not published.

| Artifact | Scope and result |
| --- | --- |
| `plan.md` | Frozen controls, timing boundary, live canary and stopping rules. |
| `live-catalog.json`, `live-status-before.json` | Public snapshots before a new canary. Gateway-wide counters are not per-run work. |
| `local/pytest.log` | Base source: 54 passed, one skipped for fast131, the production collision control, and 24-curve native CPU tests. |
| `local/metal-hardware-pytest.log` | Base source on Apple M4 Pro Metal: 33 passed, two offline compiler skips. |
| `native-controls-all24.tar.zst`, `native-controls-all24.archive.json` | Base source on CPU and M4 Pro Metal: 336 native checks, including the nine requested curves. The archive retains all 1,969 raw input/output and receipt files; its SHA-256 and receipt digest are in the adjacent manifest. |
| `local/docker-build.log`, `local/docker-check.log` | First local image build mixed an arm64 build-stage executable into an amd64 runtime. The executable could not start; its ELF machine field was 183 (AArch64). |
| `local/docker-build-amd64.log`, `local/docker-check-amd64.log` | Explicit `linux/amd64` rebuild from base: engine machine field 62 (x86_64); 168 offline packaged CPU checks passed. This is a local image, not a published digest. |
| `local/matched-cpu/`, `local/matched-metal/` | Base source, ECC2-131 walk 2. Bracketed full-invocation A/B/A and A/A rows with exact state and DP byte equality. Shared-host timing is exploratory. |
| `local/matched-metal-selective/` | Candidate source on native M4 Pro Metal: the same bounded ECC2-131 walk-2 comparison, with exact state, DP-byte, and useful-step equality. It does not exercise the Apple Paravirtual split-product path. |
| `local/x86-metal-target.log` | Base source, x86_64 process under Rosetta on local M4 Pro: the hosted-failing ECCp-131 walk-4 case passed once. |
| `local/soft-mul-tests.log`, `local/fast131-soft32-pytest.log`, `local/metal-soft32-pytest.log` | Candidate split-product arithmetic boundary check, 24 fast131 tests, and 33 M4 Pro Metal tests while split multiplication was enabled for all Metal devices. The latter took 237.72 seconds versus 93.97 seconds for the base Metal selection; those suite durations include setup and other host effects and are not a matched speed comparison. |
| `local/metal-selective-pytest.log` | Candidate selection (split product only for Apple Paravirtual device): 10 selected M4 Pro tests passed, including repeated prime walk-4 parity and source address-space checks. |
| `local/x86-metal-selective-repeat.log` | Candidate x86_64 process under Rosetta on local M4 Pro: repeated ECCp-131 walk-4 parity passed. This is not the hosted Apple Paravirtual device. |
| `local/docker-build-selective-amd64.log`, `local/docker-check-selective-amd64.log` | Candidate `linux/amd64` local image built and passed 168 network-disabled packaged CPU checks. It has not run on L4 CUDA or been published. |
| `local/matched-metal-selective-sandbox-unavailable.log` | First candidate timing attempt could not access a Metal device under the default shell sandbox; it produced no performance result. |
| `canary-plan-published/plan.json` | Dry run for nine sequential L4 canaries against the still-published immutable image. No contributor key was used. |
| `cuda-head-plan/plan.json` | Key-free dry run that checked the archived all-curve controls and bound the current CUDA source hashes. |
| `cuda-head-plan/validator-controls.json` | Local receipt-validator control accepted a complete synthetic 168-case receipt and rejected one altered input digest and one duplicate case. This is not a CUDA execution result. |
| `l4-control-e77c242/`, `l4-control-20261024.md` | Key-free NVIDIA L4 execution on PR #57 source: 168 of 168 hashes matched archived CPU controls across all 24 curves. Source, binary, device, inputs, and per-case results are bound in the receipt. |
| `gates.svg`, `report.typ`, `report.pdf` | Editable gate diagram and one-page visual report. |

The base [Worker platforms run](https://github.com/aburan28/ecc-challenges/actions/runs/40066130516)
reported an off-curve ECCp-131 walk-4 state on the hosted macOS x86_64
Apple Paravirtual Metal device in job 120789662742. Its initial 79 other
worker tests passed. A diagnostic retry of that job was requested to test
repeatability. The retry (job 120796467229) failed the same walk-4 case
with the same off-curve state after five steps. The
selective split-product path was a hypothesis about the cause. The merged PR
[#55](https://github.com/aburan28/ecc-challenges/pull/55) candidate failed
on the same device in [run 40070444197](https://github.com/aburan28/ecc-challenges/actions/runs/40070444197).
PR [#57](https://github.com/aburan28/ecc-challenges/pull/57) must pass its
hosted Apple Paravirtual check before its repair is validated.

## Commands and boundaries

The base CPU and Metal controls used the repository's locked test environment,
a Release CMake engine, and the `--cpu` and `--metal` modes of
`scripts/check_native_curves.py`. The packaged image check ran with Docker
`--platform linux/amd64 --network none` and `ECC_BACKEND=cpu`. The local
timing records use `matched_local.py`: one warm-up, three measured bracketed
repeats, 128 CPU lanes at 512 steps, and 2048 Metal lanes at 256 steps. The
candidate and baseline use identical inputs and the same walk-2 namespace.
Those timings include the Python `run_round` call and one engine process;
they exclude gateway and verifier costs. They do not establish a full
campaign rate or a new public performance claim.

The current-head key-free L4 check, after a newly rotated provider token is
configured, uses:

```sh
python3 verification/rho131-nine-curve-20261023/run_cuda_head.py \
  --out /absolute/path/to/new-cuda-control-receipts --apply
```

This key-free L4 gate compiles the current source, replays all 168 archived
controls, and checks every returned input and output digest against the
archived CPU controls, plus the engine and four source hashes. It needs a newly rotated Modal token configured outside the
repository. The dry-run command without `--apply` verifies the archive and
produces a plan without contacting Modal.

The authenticated contributor canaries use:

```sh
python3 verification/rho131-nine-curve-20261023/run_nine_canaries.py \
  --image public.ecr.aws/o1a3z1g6/ecc-challenges-worker@sha256:<approved-digest> \
  --key-file /absolute/path/to/fresh-dedicated-key \
  --out /absolute/path/to/new-canary-receipts --apply
```

The key value must stay in a mode-0600 local file and must never be pasted
into a command or report. `scripts/check_modal_contributor.py` binds each
run to the image, campaign, gateway namespace, native checkpoint, accepted
records, indexing, restart, and closure. The wrapper runs one L4 curve at a
time and retains a receipt for each; its normal mode requires a production
distinguished point. A bounded no-point attempt is censored, not counted as
an upload success. Bounded CUDA parity passed on PR #57 source. Authenticated
contribution and full contributor plus verifier cost remain open until live
receipts exist.
