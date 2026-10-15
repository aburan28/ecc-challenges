# Operations and release sequence

The original campaign AWS account is `590183823895` in `us-west-2`.
Squarespace owns `ecc-challenges.com` DNS. The existing RDS database is
`rho_dp`; this project added `ecc_public` and two limited login roles there.
The existing bucket is `ecc2k130-590183823895`. A separate AWS account is
being prepared for the public service. Deployment commands require an explicit
profile and expected account ID to avoid writing into the original account.
Do not use the database owner credential in ECS or expose S3 permissions to
contributor containers.

## Existing foundation

In the original account, `ecc-challenges-foundation` creates immutable private
ECR repositories for web, audit, and worker images; audit, index, and collision SQS queues with
dead-letter queues; and generated Secrets Manager values for the two DB roles,
key pepper, and browser session. Apply additive changes with:

```sh
aws --profile ecc2k130 cloudformation deploy --region us-west-2 \
  --stack-name ecc-challenges-foundation \
  --template-file infra/foundation.yaml
python3 scripts/bootstrap_db.py
python3 scripts/verify_db.py
```

The bootstrap above is for the original account. It uses the existing
`rho/dp-rds` owner secret locally to create the isolated schema and
permissions. That secret is never placed in an ECS
task. Re-run the bootstrap for new additive `sql/*.sql` migrations; keep old
records and migrations as evidence of what changed.

## GitHub login and certificate

Create a GitHub OAuth App with homepage `https://ecc-challenges.com` and
callback `https://ecc-challenges.com/auth/callback`. In the destination
account's AWS Secrets Manager, update `ecc-challenges/github-oauth` to JSON with `client_id` and
`client_secret`. The empty initial values make the web service fail closed.
Do not paste the secret into chat, a shell command, a PR, or a tracked file.

The original account ACM certificate
`arn:aws:acm:us-west-2:590183823895:certificate/d516b1ba-587a-40fc-98d0-22374795bdb3`
covers the apex and `www`. Add these DNS validation records in Squarespace:

| CNAME host in Squarespace | CNAME target |
| --- | --- |
| `_5276696078578ee4855b2e8460af895b` | `_39af90b8edc1eca47f9ada28606cd561.wzccmgtwzk.acm-validations.aws` |
| `_ecb4037c360a879bbc3d000db05756a8.www` | `_f8386b6f78fb605f16989025eb003b8c.wzccmgtwzk.acm-validations.aws` |

Keep these CNAMEs for renewal. The new account needs its own ACM certificate
and validation CNAMEs before its ALB can serve the domain. The release script
refuses a pending certificate or empty OAuth secret in the destination account.

## Dedicated AWS account migration

The original `public.distinguished_points` table has approximately 524 million
rows and occupies about 180 GB including indexes (read-only catalog estimate,
2026-10-14). Decide where the live index will reside before deploying service
tasks. If it stays in the original account, use private VPC connectivity and
grant only the new ECS roles access to the required S3 prefixes. The original
RDS VPC uses `172.31.0.0/16`; a new VPC for peering must not overlap it.
Match the two destination Secrets Manager database passwords to the limited
database roles and verify connectivity from the destination VPC. If the index
moves, provision a new database and import the historical index with a
checkable row-count and checksum receipt before switching collision lookup.
Neither path is established by the infrastructure template alone.

Use a fresh named AWS profile. Never pass access keys as CLI arguments or put
them in this repository. Request an ACM certificate for the apex and `www` in
the destination account and region, then add its validation CNAMEs in
Squarespace. Create the GitHub OAuth App and populate
`ecc-challenges/github-oauth` in the destination account.
Verify `aws --profile NEW_PROFILE sts get-caller-identity` reports
`NEW_ACCOUNT_ID` before creating the destination foundation stack:

```sh
aws --profile NEW_PROFILE cloudformation deploy --region us-west-2 \
  --stack-name ecc-challenges-foundation \
  --template-file infra/foundation.yaml
```

The destination account's generated database passwords do not automatically
match roles in the original database.

## Publish and deploy

The primary contributor image is already published in ECR Public at
`public.ecr.aws/d5a8v6s7/ecc-challenges-worker@sha256:a27f343edde5bcabcbc3fb6fdfc7c1981c831b467133d38773fdb0a357436b74`.
A fresh Docker configuration read that image manifest without credentials.
For a new worker source commit, publish a new immutable digest with
`python3 scripts/publish_public_worker.py`; add `--release-tag 0.1.0` only
after the release checks pass. Update the dashboard digest when the worker
source changes. The `worker-v0.1.0` GitHub tag also publishes a GHCR mirror;
if its visibility is private, use the
[package visibility settings](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
to make the mirror public. Local image publication requires Docker Desktop or
another Linux amd64 build host and a clean commit:

```sh
python3 scripts/publish_images.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID
python3 scripts/deploy_service.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID \
  --certificate-arn NEW_CERT_ARN --vpc-id NEW_VPC_ID \
  --bucket DATA_BUCKET --db-host DB_HOST --tag git-COMMITSHA
python3 scripts/deploy_service.py --profile NEW_PROFILE --account-id NEW_ACCOUNT_ID \
  --certificate-arn NEW_CERT_ARN --vpc-id NEW_VPC_ID \
  --bucket DATA_BUCKET --db-host DB_HOST --tag git-COMMITSHA --web-tasks 2 \
  --audit-tasks 2 --index-tasks 1 --reconcile-tasks 1 --resolve-tasks 1 --apply
```

Replace `git-COMMITSHA` with the 12-character tag printed by the publish
script. The service stack creates the HTTPS ALB, WAF rules, ECS services,
separate autoscaling for web/audit/index/resolve, queue alarms, and Global
Accelerator. Web and audit image references are immutable ECR digests. The
first `deploy_service.py` command plans and validates prerequisites; `--apply`
makes the AWS change. Use the output `AcceleratorIpv4Addresses` for the Squarespace apex
`A` records (both addresses), and `AcceleratorDns` for the `www` CNAME.
Remove any conflicting Squarespace default A/AAAA/CNAME records when routing
the domain. The Global Accelerator addresses remain stable across ALB changes.

Before DNS cutover, test the ALB with the intended hostname and TLS SNI using
`curl --connect-to ecc-challenges.com:443:ALB_DNS:443
https://ecc-challenges.com/health`. Require healthy targets in both zones,
GitHub sign-in/key creation, a real NVIDIA container run, a first-batch exact
replay, candidate indexing, a deliberate invalid-seed quarantine, and an
outbox/SQS restart drill. Verify the public GHCR image is pullable anonymously.
Then add the routing records and check `/health` and the dashboard through
ordinary DNS from outside AWS.

## Watch and respond

- `ecc-challenges-audit-oldest-over-hour` flags audit lag; dead-letter alarms
  cover audit, index, and collision queues. Alert on ALB 5xx, ECS unhealthy
  targets, S3 4xx/5xx, WAF blocks, RDS CPU/IOPS/connections, and rising
  `ecc_public.pressure_shards` totals.
- Treat `unverified` audit or collision states as unresolved. Inspect the
  write-once source/receipt, repair the verifier or solver, and append a new
  receipt before retrying. Never relabel a timeout as a mathematical failure.
- If verified demand exceeds measured drain, reduce admission quotas or set
  web tasks to zero after preserving queued work; do not delete S3 objects,
  cursor rows, dead-letter messages, or candidate quarantine evidence.
- The existing campaign's RDS security group has a world-open 5432 rule.
  Inventory its legacy clients and move them to private or narrowly scoped
  ingress in a separate coordinated change.

The public gateway does not import old campaign points into its own table.
It reads the existing campaign index for matches, so old data contributes to
collision detection without a destructive migration or duplicate storage.
