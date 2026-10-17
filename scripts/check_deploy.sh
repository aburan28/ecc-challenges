#!/bin/sh
# Static checks for deploy/: Kubernetes schemas, compose files, systemd units
# and shell scripts. CI runs this; it also works locally if the tools exist.
set -eu
cd "$(dirname "$0")/.."

kubeconform=${KUBECONFORM:-kubeconform}
key=deploy/kubernetes/base/api-key
placeholder=no
# Render with a placeholder key so no real secret is needed.
if [ ! -e "$key" ]; then printf placeholder > "$key"; placeholder=yes; fi
trap '[ "$placeholder" = yes ] && rm -f "$key"' EXIT
for overlay in cpu gpu; do
  echo "kubernetes/$overlay"
  kubectl kustomize "deploy/kubernetes/$overlay" \
    | "$kubeconform" -strict -summary -kubernetes-version 1.34.0 -
done

echo "compose"
ECC_UID=1000 ECC_GID=1000 ECC_WORKER_IMAGE=example.invalid/worker \
  docker compose -f deploy/docker/compose.yaml --profile cpu --profile gpu config -q
docker compose -f deploy/local/compose.yaml --profile worker config -q

echo "systemd"
if [ ! -x /opt/ecc-challenges/bin/ecc-worker ]; then
  sudo install -D -m 0755 /dev/null /opt/ecc-challenges/bin/ecc-worker
fi
systemd-analyze verify deploy/systemd/ecc-worker@.service deploy/systemd/ecc-worker-docker@.service

echo "shell"
shellcheck deploy/native/install.sh deploy/hpc/ecc-worker.sbatch scripts/check_deploy.sh
echo "deploy checks passed"
