#!/bin/sh
# Install the ECC Challenges worker on a Linux host without containers.
#
#   sudo deploy/native/install.sh            # build, install, add systemd unit
#   deploy/native/install.sh --prefix "$HOME/.local/ecc-challenges" --no-systemd
#
# Needs python3 (3.10+) and a C++17 compiler with OpenMP (g++ or clang++).
# If nvcc is on PATH, a CUDA build is made too (set CUDA=no to skip, and
# NVCC_ARCH to pick a GPU architecture; the default builds for this host's GPU).
set -eu

prefix=/opt/ecc-challenges
systemd=auto
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix=$2; shift 2 ;;
    --no-systemd) systemd=no; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$prefix" in
  /?*) ;;
  *) echo "--prefix must be an absolute path" >&2; exit 2 ;;
esac
repo=$(cd "$(dirname "$0")/../.." && pwd)
cxx=${CXX:-$(command -v g++ || command -v clang++ || true)}
[ -n "$cxx" ] || { echo "need g++ or clang++" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "need python3 3.10 or newer" >&2; exit 1; }

echo "building CPU engine with $cxx"
mkdir -p "$prefix/bin"
# shellcheck disable=SC2086 # CXXFLAGS holds several flags on purpose
"$cxx" -std=c++17 -O2 -fopenmp ${CXXFLAGS:-} -x c++ "$repo/worker/generic/engine.cu" \
  -o "$prefix/bin/generic-engine"
"$prefix/bin/generic-engine" --self-test

if [ "${CUDA:-auto}" != no ] && command -v nvcc >/dev/null 2>&1; then
  echo "building CUDA engine with nvcc"
  nvcc -std=c++17 -O2 -arch="${NVCC_ARCH:-native}" -Xcompiler -fopenmp \
    "$repo/worker/generic/engine.cu" -o "$prefix/bin/generic-engine.cuda"
  "$prefix/bin/generic-engine.cuda" --self-test
  mv "$prefix/bin/generic-engine.cuda" "$prefix/bin/generic-engine"
elif [ "${CUDA:-auto}" = yes ]; then
  echo "CUDA=yes but nvcc was not found" >&2; exit 1
fi

echo "installing client to $prefix"
rm -rf "${prefix:?}/lib"
mkdir -p "$prefix/lib/ecc_challenges"
cp "$repo/worker/client.py" "$repo/worker/protocol.py" "$prefix/lib/"
cp -R "$repo/worker/generic" "$prefix/lib/generic"
cp "$repo/src/ecc_challenges/__init__.py" "$prefix/lib/ecc_challenges/"
cp -R "$repo/src/ecc_challenges/multicurve" "$prefix/lib/ecc_challenges/multicurve"
find "$prefix/lib" -name __pycache__ -prune -exec rm -rf {} +
cat > "$prefix/bin/ecc-worker" <<WRAPPER
#!/bin/sh
# Settings come from the environment; see docs/running-a-worker.md.
export ECC_GENERIC_ENGINE="\${ECC_GENERIC_ENGINE:-$prefix/bin/generic-engine}"
export ECC_PROTOCOL="\${ECC_PROTOCOL:-2}"
export ECC_API_URL="\${ECC_API_URL:-https://ecc-challenges.com}"
exec python3 -B "$prefix/lib/client.py" "\$@"
WRAPPER
chmod 0755 "$prefix/bin/ecc-worker"

if [ "$systemd" = auto ] && [ "$(id -u)" = 0 ] && [ -d /run/systemd/system ]; then
  install -m 0644 "$repo/deploy/systemd/ecc-worker@.service" /etc/systemd/system/
  sed -i "s|/opt/ecc-challenges|$prefix|g" /etc/systemd/system/ecc-worker@.service
  id ecc-worker >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin ecc-worker
  mkdir -p /etc/ecc-challenges
  [ -f /etc/ecc-challenges/gpu0.env ] || install -m 0644 "$repo/deploy/systemd/example.env" /etc/ecc-challenges/gpu0.env
  systemctl daemon-reload
  cat <<NEXT

Installed. Next:
  1. Save your API key:  sudo install -m 0600 /dev/stdin /etc/ecc-challenges/api-key
  2. Edit /etc/ecc-challenges/gpu0.env (curve, backend, GPU index)
  3. sudo systemctl enable --now ecc-worker@gpu0
  4. journalctl -fu ecc-worker@gpu0
NEXT
else
  cat <<NEXT

Installed to $prefix. Run a worker in the foreground with:
  ECC_CAMPAIGN=eccp-131 ECC_STATE_DIR=\$HOME/ecc-data/eccp-131 \\
  ECC_API_KEY_FILE=\$HOME/.config/ecc-challenges/api-key $prefix/bin/ecc-worker
NEXT
fi
