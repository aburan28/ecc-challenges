# Published worker validation and catalog rollout, 2026-10-21

This is an operational observation for the optimized binary rho worker in
merged source commit `9e04a19e9862945a90098f3a5b377448e29a6819`. It
does not assign a research status or a complete-campaign performance result.

The [frozen plan](plan.md), [CodeBuild receipt](codebuild.json),
[S3 release manifest](release-manifest.json), and [object metadata](manifest-object.json)
bind the exact build and its immutable worker digest
`public.ecr.aws/o1a3z1g6/ecc-challenges-worker@sha256:a347a784935a7713b204335e8316f780a5c3209d64cc372980c1b35bee9f81ba`.
The builder succeeded after its native worker self-test.

Command: `ECC_RELEASE_IMAGE=<manifest worker digest> modal run
scripts/check_release_image.py --out verification/binary-rho-release-20261021/l4-image-check.json`.
The committed launcher hash is in the plan. Modal app
`ap-xXidiXPwfQZFlYiH3k9BpU`, sandbox
`sb-Uyf9x8WMmOMqVTPQ3Ztyxf`, ran on NVIDIA L4 driver `610.57.04` as
UID/GID 10001. The [complete receipt](l4-image-check.json) reports 168/168
archived CUDA controls passed across 24 campaigns, including 63/63 controls
for the requested nine binary curves. All nine positive DP1 cases emitted
nonempty output, with exact bytes equal to archived CPU controls. The engine
binary SHA-256 in the image was
`4387416cd723be221193d73dfc521092960536a739b4cf73afc886ff855fe99c`.
The in-container check took 23.18 seconds. Modal scheduling took substantially
longer; that wait is outside the receipt's elapsed time. See the
[graph](l4-nine-curves.svg), [editable graph source](render_image_check.py),
[PDF source](report.typ), and [inspected PDF](report.pdf).

The [CloudFormation change set](change-set.json) changed the public worker
image parameter only. Its resource effects were a replacement WebTask
definition and WebService rollout; no audit, index, database, or queue resource
was changed. After execution, the [stack snapshot](service-stack.json) was
`UPDATE_COMPLETE`, the [web service snapshot](web-service.json) had two of two
running tasks at a completed deployment, and both [ALB targets](target-health.json)
were healthy. The [live public catalog](live-catalog.json) advertised the
manifest's worker digest and protocol 2 for all nine requested curve IDs.
`ecc2k-130` retained its legacy contribution label and listed protocols 1
and 2; the other eight listed `generic_cpu_cuda_v2`.

This establishes published-image CUDA correctness for the finite controls and
successful catalog rollout. An authenticated nine-curve contribution, server
acceptance, checkpoint restart, sustained useful-step and distinguished-point
rate, verifier cost, and full pipeline A/B comparison remain to be measured.
The matched native L4 worker invocation gain is recorded separately under
`verification/binary-rho-profile-20261021/`; its timing cannot be substituted
for a complete-campaign gain. No contributor credential was used in this
release check.
