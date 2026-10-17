# Operations and release sequence

The public platform belongs in its own AWS account in us-west-2. Squarespace
manages DNS for ecc-challenges.com. The original ECC2K campaign remains in
account 590183823895; do not connect the public service to its RDS instance or
S3 bucket. The deployment scripts require a named AWS profile and the expected
12-digit account ID, then verify the active AWS identity before writing.
Configure credentials locally and rotate any credential that was exposed.
Never put a secret value in chat, a shell command, a PR, or a tracked file.

## What the new account owns

- The foundation stack: private ECR repositories, three durable queues with
  dead-letter queues, and generated application secrets.
- The data stack: a dedicated VPC, a private encrypted Multi-AZ PostgreSQL
  instance, a versioned private S3 archive, an empty historical-point table,
  a GitHub App sign-in secret placeholder, and a one-off ECS administration task.
- The service stack: HTTPS ALB, WAF rate rules, stateless web and queue workers,
  separate autoscaling, queue alarms, and Global Accelerator.

The historical-point table starts empty. New public uploads are archived,
audited, indexed, and matched against other new public points. Matches with the
original campaign's existing points are unavailable until the historical import
and backmatch below have run. This is not a reason to leave the new index queue
stopped: every new batch and candidate is preserved, and the backmatch covers
the interval before import.

The old campaign's run registry currently has no entries in the public range
50000–65534 (read-only check on 2026-10-14). Reserve that range for the public
service in both accounts. The isolated registry uses the same format but has
its own S3 bucket; do not let the original allocator use public run IDs.

## Historical point import

`scripts/historical_import.py` copies the original campaign's points into
`public.distinguished_points` in one direction. It never connects the public
service to the original account: the operator runs the export with a read-only
profile for account 590183823895, and the public database never sees that
account's credentials. The original S3 `dp/` corpus is the source, because the
original RDS table is a derived view that can lag it.

1. `export --dry-run` lists and checks the whole corpus and writes nothing. It
   refuses any object whose `.bin.json` marker or key sha256 disagrees with its
   body, and drops (and counts) records that are not canonical DP32 keys or whose
   seed falls in the public run range 50000–65534. Read its summary before going on.
2. `export --parts s3://<public archive>/historical-import/<date>` writes each
   source object as a checksum-bound part with a JSON sidecar. It resumes by
   skipping source keys that already have a sidecar.
3. `load` runs with the database owner's DSN in `HISTORICAL_DSN`. Each part is
   re-hashed, inserted with `ON CONFLICT DO NOTHING`, and recorded in
   `ecc_public.historical_import_parts` in the same transaction, so a rerun skips
   finished parts. Two historical seeds reaching one point are kept in
   `ecc_public.historical_conflicts`; any row there is a collision in the
   original campaign and needs the solver.
4. `backmatch` runs after the last load. Batches indexed before their point was
   imported never joined against it; it writes the `campaign` leads that
   `index_one` would have written, and the resolver picks them up as usual. It is
   idempotent. Run it again after any later load.
5. `reconcile` (optional) compares per-worker counts with the original RDS view
   through a read-only `SOURCE_DSN`. Workers short of the source there need a
   look; workers ahead of it are the view lagging S3.

Size the database before loading: every historical point is one
`distinguished_points` row of about 90 bytes with its primary key index.

## 1. Deploy the foundation and private data stack

Run from a clean, committed checkout. Set up a fresh named AWS CLI profile for
the new account. Check that STS reports the expected account:

    aws --profile NEW_PROFILE sts get-caller-identity --query Account --output text

The first deploy creates the foundation, VPC, private database, archive, and
admin ECR repository. It also creates an empty OAuth secret. The scripts print
resource IDs and ARNs, never secret values.

    python3 scripts/deploy_data.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID
    python3 scripts/deploy_data.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID --apply

The admin image contains the SQL migrations and database bootstrap. Publish
it from the same clean commit, then update the data stack using the immutable
digest that the publisher prints:

    python3 scripts/publish_admin.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID
    python3 scripts/deploy_data.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID \
      --admin-image-uri ADMIN_IMAGE_DIGEST --apply
    python3 scripts/run_admin.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID
    python3 scripts/run_admin.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID --apply
    python3 scripts/init_registry.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID --apply

The admin task runs in a public subnet with an outbound public IP. Its RDS
connection stays inside the dedicated VPC and uses verified TLS. It reads the
RDS managed master secret and generated role passwords through its task role,
installs the public schema, grants narrow privileges, and verifies both
application logins. A nonzero task exit is a failed bootstrap; inspect the
/ecc-challenges/admin CloudWatch log group, fix the cause, and rerun the
idempotent task. The application services must wait for a successful task.

The registry initializer writes only the new account's ready marker using a
conditional S3 write. It refuses a wrong account, bucket, or incompatible
existing marker.

## 2. Configure GitHub login and TLS

Use a public GitHub App for contributor sign-in, with homepage
https://ecc-challenges.com and callback
https://ecc-challenges.com/auth/callback. The site needs only the authenticated
user's numeric ID and login from `GET /user`; this endpoint requires no GitHub
App permissions. Disable the webhook unless another feature needs it, and do
not grant repository or organization permissions for sign-in. A user can
authorize the app without installing it. The App ID and private key are not
used by this sign-in flow.

In the new account's Secrets Manager, update `ecc-challenges/github-oauth` to
a JSON object with `client_id` and `client_secret` from that GitHub App. Keep
the client secret in Secrets Manager; never paste it into chat or source.
The empty initial values make service deployment fail closed. The web flow
does not request OAuth scopes because GitHub Apps use their configured
permissions.

GitHub documents this [sign-in flow](https://docs.github.com/en/apps/creating-github-apps/writing-code-for-a-github-app/building-a-login-with-github-button-with-a-github-app)
and the [permission-free `GET /user` endpoint](https://docs.github.com/en/rest/users/users#get-the-authenticated-user).

Request an ACM certificate in us-west-2 for ecc-challenges.com and
www.ecc-challenges.com. Add the certificate's DNS validation CNAMEs in
Squarespace and wait until ACM reports ISSUED. DNS records for the original
account's certificate do not establish validation for this new certificate.
The release script checks the certificate account, region, status, and both
hostnames.

## 3. Publish contributor and service images, then deploy

Publish the contributor image into the new account's ECR Public registry. The
publisher creates the repository if needed, verifies its owner, refuses an
existing tag, and prints a pinned digest. ECR Public uses the us-east-1 API
endpoint even when the service stack is in us-west-2.

    python3 scripts/publish_public_worker.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID

Pull the printed digest anonymously from a fresh Docker configuration. Use
that digest as NEW_PUBLIC_WORKER_DIGEST below. Build and publish the web and
audit images from the same clean commit:

    python3 scripts/publish_images.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID

Read VpcId, DataBucketName, and DatabaseHost from the ecc-challenges-data
CloudFormation outputs. The service deployer checks all three against that
stack and checks its generated DB password secrets against the foundation.
The first command is a read-only plan. Replace the placeholders with the
reported IDs, certificate ARN, and image tag:

    python3 scripts/deploy_service.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID \
      --certificate-arn NEW_CERT_ARN --public-worker-image NEW_PUBLIC_WORKER_DIGEST \
      --vpc-id NEW_VPC_ID \
      --bucket DATA_BUCKET --db-host DB_HOST --tag git-COMMITSHA
    python3 scripts/deploy_service.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID \
      --certificate-arn NEW_CERT_ARN --public-worker-image NEW_PUBLIC_WORKER_DIGEST \
      --vpc-id NEW_VPC_ID \
      --bucket DATA_BUCKET --db-host DB_HOST --tag git-COMMITSHA \
      --web-tasks 2 --audit-tasks 2 --index-tasks 1 \
      --reconcile-tasks 1 --resolve-tasks 1 --apply

Add `--alert-email ADDRESS` to the deploy command to subscribe an operator to
audit-age, index-age, and dead-letter alarms. The recipient must confirm the
Amazon SNS subscription email before notifications can arrive. The alert topic
and alarm actions exist even when no email is configured; an unconfirmed or
missing subscription is not an operational alert path. Check the subscription
status with `aws sns list-subscriptions-by-topic` using the service stack's
`AlertTopicArn` output, and verify alarm actions before DNS cutover.

Before routing DNS, exercise the ALB with the intended hostname and TLS SNI:

    curl --connect-to ecc-challenges.com:443:ALB_DNS:443 \
      https://ecc-challenges.com/health

Require healthy web targets in both zones; GitHub sign-in and key creation; a
real NVIDIA contributor container run; first-batch exact replay; indexing of
new public candidates; invalid-seed quarantine; and an outbox/SQS restart
drill. Verify the contributor image can be pulled anonymously. The dashboard
uses the same pinned public digest that the service deployer verified belongs
to the new account.

The service stack outputs two Global Accelerator IPv4 addresses and its DNS
name. Set both apex A records in Squarespace, use the accelerator DNS name for
the www CNAME, and remove conflicting Squarespace defaults. Verify /health,
login, and the dashboard through ordinary DNS from outside AWS. The addresses
stay stable across ALB changes.

## 4. Watch and respond

- Watch ALB 5xx, ECS target health, WAF blocks, S3 errors, RDS CPU/IOPS and
  connections, queue age/dead-letter counts, and pressure-shard totals.
- The oldest-audit-message alarm and all three dead-letter queues make a
  growing ingest backlog visible. Measure producer arrival against audit and
  index drain before raising admission limits.
- Treat unverified audits and collision leads as unresolved. Preserve the
  write-once input and receipt, then retry after the verifier or solver is
  repaired. A timeout is not a negative mathematical finding.
- If arrivals exceed sustained drain, lower contributor quotas or suspend
  admission after preserving queued work. Keep S3 objects, cursor rows,
  dead-letter messages, and candidate quarantine evidence.

Until the historical import above has run and its backmatch has finished,
describe collision coverage as new-public-to-new-public only. While the original
fleet is still writing, catch up by rerunning export, load and backmatch: export
skips source keys it has already copied and picks up new ones.

## All-curve release builds and canary gate

`infra/build.yaml` provisions one bounded Linux CodeBuild release builder. It
clones an explicit public commit, verifies HEAD, builds the four images, runs
the native self-test, and writes pinned digests to
`release-builds/<commit>/manifest.json` in the isolated bucket. The build role
has image-push and release-manifest permissions, with no database-secret access.
No contributor credential enters that build context.

Deploy migration 003 and worker/web images before enabling v2. A rollout can set
`MulticurveCanaryKey` to the ID portion of the designated key while
`MulticurveEnabled=false`. Only that authenticated key can use v2; public catalog
availability stays unchanged. The worker queue still processes its batches.
After combined-container, contributor and capacity gates, enable v2 and clear
the canary parameter. Keep existing v1 workers on their bound namespace.

### Retiring a generic contributor run

An ordinary container stop preserves its run for resumption. To switch a worker
permanently to another challenge, stop it gracefully and then launch the same
pinned image, key, campaign and state volume with `ECC_PROTOCOL=2` and
`ECC_ACTION=close`. This authenticates ownership and retires the run without
removing its checkpoints, uploaded points or verification work. The command
refuses a local pending outbox; resume normal work to flush it first. An unfinished
server upload intent must commit or expire before closure. Closed runs remain
readable and their admitted batches continue processing. Use a new state volume
for a new run. Closing releases an active slot; it does not reset the account's
lifetime anti-abuse limit.

### V2 admission and verifier scaling

`--max-pending-batches` is an explicit per-protocol deployment bound (initially
512), sharded across sixteen pressure rows. At the maximum 1 MiB batch size this
bounds v2 pending raw payload near 512 MiB; account and daily-byte quotas apply
as well. Choose the bound from measured drain time. Contributors keep their
outbox and pause on retryable refusal.

The audit pool performs all v2 relation checks and DynamoDB insertion. The
separate legacy index pool does not add v2 processing capacity. Audit scale-out
uses visible queue depth and CPU utilization, up to 32 tasks. Automatic audit
scale-in is disabled during the initial rollout to avoid interrupting long,
in-flight verification batches. Review pending work and retained leases before
an operator reduces the pool. This can leave extra tasks running after a burst;
monitor cost and tune after the sustained capacity gate. The existing
`scripts/capacity.py` models the legacy sampled verifier and cannot establish v2
throughput. A production DP/s claim requires measurements of every distinct
witness under the deployed curve mix and fleet size.

### Switching the point index to compact S3 parts

The compact index (see `docs/multicurve-storage.md`, "Compact point index")
replaces DynamoDB items with sorted 16-byte entries in S3.

1. Update the data stack so the bucket gets the lifecycle rule for superseded
   parts (`scripts/deploy_data.py`).
2. Run the database bootstrap so `005_compact_point_index.sql` and its grant
   apply.
3. Deploy the service with `--point-index-mode compact` and keep
   `--reconcile-tasks 1`. In compact mode the reconcile task grows to 1 vCPU and
   4 GB, and merges every `--point-index-merge-seconds` (default 3600).
4. Watch the reconcile log for `point-index-merge` lines. Each one names the
   generation, the entry count and any `collision_leads`.

Batches indexed into DynamoDB earlier stay there, so switch before public
contribution starts. If the index is ever in doubt, call
`Merger.rebuild(namespace_id)` from an administration task: the next merges
rebuild it from the raw batches.

### Public image mirrors

ECR Public anonymous consumers have a fixed monthly data allowance. Shared cloud
provider importers can exhaust that allowance before starting a container.
`infra/registry.yaml` supplies a public-image authentication role with no push,
private-image, bucket, database or application-secret permissions. Its GitHub
OIDC trust binds the repository's verified immutable IDs and the release refs.

The existing contributor-image workflow accepts an optional `source_image`
dispatch input. It copies the exact ECR image to
`ghcr.io/aburan28/ecc-challenges-worker` with digest preservation and checks an
anonymous pull. First-time GHCR package publication can require the package
owner to make it public in GitHub's package settings. Do not advertise the
mirror until the anonymous check passes. The deployment helper requires the
mirror's manifest bytes to match an image digest in the isolated AWS account.

Internal Modal checks can optionally use a mode-0600 `ECC_REGISTRY_AUTH_FILE`
containing a temporary pull-only token. Sending that token to a third-party
importer requires explicit authorization; an automatic approval rejection does
not authorize another transport. No AWS access keys belong in a contributor
container. The public release must work with an anonymous provider pull.
