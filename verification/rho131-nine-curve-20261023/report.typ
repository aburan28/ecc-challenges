#set page(paper: "a4", margin: 20mm)
#set text(font: "Arial", size: 10pt)
#set heading(numbering: none)

= Rho131 current-head and nine-curve validation

Snapshot: 2026-10-23. Base source commit
`b78fc2921e9b9a60174e1693aff15c7daf23f9c7`. The public catalog
currently pins `sha256:a347a784935a7713b204335e8316f780a5c3209d64cc372980c1b35bee9f81ba`;
that image predates this base commit. The report is an execution snapshot, not
a release or a full-campaign performance claim.

#image("gates.svg", width: 100%)

== Observed controls

On the Apple M4 Pro, the base source passed 54 CPU tests with one skip.
The Metal parity selection passed 33 tests with two offline-toolchain skips.
The native control runner passed 336 CPU/Metal checks across all 24 exact
instances, including all nine requested binary curves. A local amd64 image
built from the base source compiled and passed its native self-test; its
packaged, network-disabled CPU check passed 168 controls. These are distinct
from L4 CUDA execution of the new source.

The first local Docker build mixed an arm64 engine build stage with an amd64
runtime stage. Its offline check failed to start the engine. An explicit
`linux/amd64` build corrected the executable architecture, then the packaged
check passed. Both build logs are retained. This was a build invocation
error; the corrected image has not been published.

The current-head GitHub worker-platform run reported one failing case:
ECCp-131 walk 4 on the macOS x86_64 Apple Paravirtual Metal device produced
an off-curve state at five steps. The same targeted case passed with an
x86_64 process under Rosetta on the local M4 Pro. That distinguishes the two
observed devices but does not establish the cause. A 32-bit-only Metal
multiplication path and a repeated parity control are under validation.
The candidate passed the repeated case on the local M4 Pro and a packaged
amd64 CPU self-check with 168 controls. Hosted Apple Paravirtual and L4 CUDA
correctness remain to be checked.

== Exploratory matched invocation timing

The frozen walk-2 ECC2-131 inputs were run on the base source with bracketed
reference and specialised engine calls, plus a reference A/A control.
All compared output bytes and actual step counts matched. Raw timings and
ratios are retained in `local/matched-cpu/` and `local/matched-metal/` as
exploratory observations on a shared host. They exclude registration,
network, distinguished-point upload, server verification and provider
billing. No performance advantage is promoted from these rows. The proposed
Metal source was also measured on the native M4 Pro path with exact output and
useful-step equality. Its Apple Paravirtual variant remains unmeasured.

== Open gates

Current-head hosted CI and current-head L4 CUDA parity remain open. The
authenticated nine-curve canary needs a newly rotated provider token and a
fresh dedicated contributor key stored in a local mode-0600 file. Its planned
launcher uses one L4 sandbox at a time, a fresh volume per curve, immutable
image identity, authenticated per-run status, checkpoint restart, accepted
records, index completion and closure. A no-point attempt is retained as a
censored result. Full contributor and verifier cost comparisons remain open
until those runs produce bound receipts.

The artifact index is `README.md`; the hosted failure is GitHub Actions run
40066130516, job 120789662742. The editable diagram is `gates.svg`.
