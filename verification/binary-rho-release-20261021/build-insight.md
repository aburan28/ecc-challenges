# Commit-pinned worker build, 2026-10-21

The bounded `ecc-challenges-release` CodeBuild job
`ecc-challenges-release:92708fb3-7741-4226-9e75-281f7468b9c5` succeeded
in AWS account `104186811093`, region `us-west-2`. The job cloned and checked
out merge commit `9e04a19e9862945a90098f3a5b377448e29a6819`, built the
service images and public worker image, ran the worker's native self-test,
then published [the pinned manifest](release-manifest.json). The retained
[phase receipt](codebuild.json) records the start and end, every phase outcome,
and the CloudWatch log link.

The manifest was read from S3 key
`release-builds/9e04a19e9862945a90098f3a5b377448e29a6819/manifest.json`
in bucket `ecc-challenges-data-104186811093`. Its [object metadata](manifest-object.json)
binds version `7MBvqcKvjmkfqy4_98DEhoxqY7XIn2WX`, with AES256 encryption.
It names public worker digest
`sha256:a347a784935a7713b204335e8316f780a5c3209d64cc372980c1b35bee9f81ba`;
ECR Public independently returned this digest with tag
`allcurves-9e04a19e9862` and push time `2026-10-21T17:26:18.823Z`.

The build establishes a reproducible image artifact and a passed native
self-test. GPU behavior belongs to the final-image L4 control receipt; the
build does not measure worker throughput or a complete campaign. At the time
of this record, the merge commit's main-branch GitHub CI runs were queued.
