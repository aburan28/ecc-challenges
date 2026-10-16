#!/usr/bin/env bash
set -euo pipefail
[[ "$(git rev-parse HEAD)" == "$RELEASE_COMMIT" ]]
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$RELEASE_ACCOUNT" ]]
RELEASE_TAG="allcurves-${RELEASE_COMMIT:0:12}"
PRIVATE_REGISTRY="$RELEASE_ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com"
aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$PRIVATE_REGISTRY"
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws
for component in web admin audit; do
  docker build -f "Dockerfile.$component" -t "$PRIVATE_REGISTRY/ecc-challenges-$component:$RELEASE_TAG" .
  docker push "$PRIVATE_REGISTRY/ecc-challenges-$component:$RELEASE_TAG"
done
docker build -f worker/Dockerfile -t "$PUBLIC_WORKER_REPOSITORY:$RELEASE_TAG" .
docker run --rm --entrypoint /opt/ecc/generic-engine "$PUBLIC_WORKER_REPOSITORY:$RELEASE_TAG" --self-test
docker push "$PUBLIC_WORKER_REPOSITORY:$RELEASE_TAG"
python scripts/release_manifest.py
