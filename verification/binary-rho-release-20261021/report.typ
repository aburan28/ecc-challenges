#set page(paper: "a4", margin: 22mm)
#set text(font: "Arial", size: 10pt)
#set heading(numbering: none)

= Published binary rho worker image check

2026-10-21. Source commit `9e04a19e9862945a90098f3a5b377448e29a6819`.
Public ECR worker digest `sha256:a347a784935a7713b204335e8316f780a5c3209d64cc372980c1b35bee9f81ba`.

The immutable image passed 168 of 168 archived CUDA controls on an NVIDIA L4,
across all 24 challenge curves, as UID/GID 10001. The nine requested binary
curves each passed seven controls and emitted nonempty distinguished point
output in the positive DP1 case. Exact output bytes matched the archived CPU
controls. The receipt records the engine binary hash, commands, device,
per-case output hashes, failures, and sandbox ID.

#image("l4-nine-curves.svg", width: 100%)

The bars report output bytes for one frozen positive control. They are neither
campaign point rates nor comparative performance measurements. This validation
does not exercise authenticated contribution, network transport, server
verification, or sustained collection. The 23.18 second receipt duration is
the sandbox's in-container work; provider scheduling time is outside it.

Artifacts: `l4-image-check.json` (raw receipt), `release-manifest.json`,
`codebuild.json`, `manifest-object.json`, `render_image_check.py`, and this
editable PDF source. The matched native L4 arithmetic benchmark is in
`verification/binary-rho-profile-20261021/`.
