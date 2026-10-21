# Binary rho published-image validation plan, 2026-10-21

The source is merge commit `9e04a19e9862945a90098f3a5b377448e29a6819`
(PR #48). Build the exact commit with the existing `ecc-challenges-release`
CodeBuild project in AWS account `104186811093`, `us-west-2`, and retain the
build ID, phase outcomes, log link, S3 release manifest, and immutable image
digests. The builder performs the worker native self-test before publication.
Its image tags are candidate artifacts; this plan does not deploy them.

For the manifest's public worker digest, run the committed
`scripts/check_release_image.py` (SHA-256
`46b3304efbf64b1f835dd186cd23414876decce04424d25dc45b46a49c68d198`)
on one Modal L4 sandbox, UID/GID 10001, with no contributor credential. Its
frozen control archive SHA-256 is
`b0807a28e8757993103ffb20b117e9d8b155517beb738089d3ab8285160e6cdb`.
The scripted limit is 1200 seconds. Retain the complete JSON receipt including
environment, engine hash, command results, error cases and sandbox ID.

Acceptance: the builder succeeds, the manifest binds the source commit and
worker digest, the image self-test and imports succeed, and all 168 frozen GPU
controls across all 24 campaigns match their archived CPU outputs. Every
`mixed-dp1-cpu` case must emit at least one distinguished point. The nine
requested binary curves are a subset of those 24. A failed build or check is
recorded with its exact phase and output and leaves the published-image gate
open. No result from this plan measures collection, network, verification, or
sustained campaign throughput. A real authenticated contribution and capacity
measurement remain separate rollout gates.
