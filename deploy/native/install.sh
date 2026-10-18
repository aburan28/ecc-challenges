#!/bin/sh
# Install the ECC Challenges worker on Linux or macOS without containers.
#
#   sudo deploy/native/install.sh            # Linux: build, install, add systemd unit
#   deploy/native/install.sh --prefix "$HOME/.local/ecc-challenges" --no-systemd
#
# Needs python3 (3.10+) and a C++17 compiler (g++ or clang++); OpenMP is used
# when the compiler has it. If nvcc is on PATH, a CUDA build is made too (set
# CUDA=no to skip, and NVCC_ARCH to pick a GPU architecture; the default builds
# for this host's GPU). On macOS (Apple silicon or Intel) the engine is built
# with Metal as well (set METAL=no to skip) and a launchd agent is prepared.
# Windows uses deploy/native/install.ps1.
set -eu

prefix=/opt/ecc-challenges
systemd=auto
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix=$2; shift 2 ;;
    --no-systemd) systemd=no; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$prefix" in
  /?*) ;;
  *) echo "--prefix must be an absolute path" >&2; exit 2 ;;
esac
os=$(uname -s)
repo=$(cd "$(dirname "$0")/../.." && pwd)
cxx=${CXX:-$(command -v g++ || command -v clang++ || true)}
[ "$os" = Darwin ] && cxx=${CXX:-$(command -v clang++ || true)}
[ -n "$cxx" ] || { echo "need g++ or clang++ (on macOS: xcode-select --install)" >&2; exit 1; }
# The wrapper runs this exact interpreter: launchd and systemd do not use the
# login PATH, and macOS's /usr/bin/python3 can be older than 3.10.
python=${PYTHON:-$(command -v python3 || true)}
if [ -z "$python" ] || ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "need python3 3.10 or newer (set PYTHON to choose one)" >&2; exit 1
fi
python=$("$python" -c 'import sys; print(sys.executable)')

threads=
[ "$os" = Linux ] && threads=-pthread
openmp=-fopenmp
if ! printf 'int main() { return 0; }\n' \
    | "$cxx" -x c++ -fopenmp - -o /dev/null >/dev/null 2>&1; then
  openmp=
  echo "$cxx has no OpenMP; the CPU backend uses std::thread instead"
fi

echo "building CPU engine with $cxx"
mkdir -p "$prefix/bin"
# shellcheck disable=SC2086 # CXXFLAGS, openmp and threads hold flags on purpose
"$cxx" -std=c++17 -O2 $openmp $threads ${CXXFLAGS:-} -x c++ \
  "$repo/worker/generic/engine.cu" -o "$prefix/bin/generic-engine"
"$prefix/bin/generic-engine" --self-test

if [ "${CUDA:-auto}" != no ] && command -v nvcc >/dev/null 2>&1; then
  echo "building CUDA engine with nvcc"
  # shellcheck disable=SC2086
  nvcc -std=c++17 -O2 -arch="${NVCC_ARCH:-native}" ${openmp:+-Xcompiler $openmp} \
    "$repo/worker/generic/engine.cu" -o "$prefix/bin/generic-engine.cuda"
  "$prefix/bin/generic-engine.cuda" --self-test
  mv "$prefix/bin/generic-engine.cuda" "$prefix/bin/generic-engine"
elif [ "${CUDA:-auto}" = yes ]; then
  echo "CUDA=yes but nvcc was not found" >&2; exit 1
fi

if [ "$os" = Darwin ] && [ "${METAL:-auto}" != no ]; then
  echo "building Metal engine with $cxx"
  generated=$(mktemp -d)
  # The kernel is compiled at run time from ec_core.h, embedded as a string.
  {
    printf 'static const char core_source[] = R"ECCMSL(\n'
    cat "$repo/worker/generic/ec_core.h"
    printf ')ECCMSL";\n'
  } > "$generated/metal_core_source.inc"
  # shellcheck disable=SC2086
  "$cxx" -std=c++17 -O2 -fobjc-arc ${CXXFLAGS:-} -I"$generated" \
    "$repo/worker/generic/engine_metal.mm" -framework Metal -framework Foundation \
    -o "$prefix/bin/generic-engine.metal"
  rm -rf "$generated"
  "$prefix/bin/generic-engine.metal" --self-test
  mv "$prefix/bin/generic-engine.metal" "$prefix/bin/generic-engine"
fi
"$prefix/bin/generic-engine" --probe

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
exec "$python" -B "$prefix/lib/client.py" "\$@"
WRAPPER
chmod 0755 "$prefix/bin/ecc-worker"

if [ "$os" = Darwin ]; then
  plist="$prefix/com.ecc-challenges.worker.plist"
  sed -e "s|@PREFIX@|$prefix|g" -e "s|@HOME@|$HOME|g" \
    "$repo/deploy/launchd/com.ecc-challenges.worker.plist" > "$plist"
  cat <<NEXT

Installed to $prefix. Run a worker in the foreground with:
  ECC_CAMPAIGN=eccp-131 ECC_STATE_DIR=\$HOME/ecc-data/eccp-131 \\
  ECC_API_KEY_FILE=\$HOME/.config/ecc-challenges/api-key $prefix/bin/ecc-worker

Or keep one running in the background with launchd:
  1. Edit $plist (curve, state directory)
  2. cp "$plist" ~/Library/LaunchAgents/
  3. launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.ecc-challenges.worker.plist
  4. tail -f ~/Library/Logs/ecc-worker.log
NEXT
elif [ "$systemd" = auto ] && [ "$(id -u)" = 0 ] && [ -d /run/systemd/system ]; then
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
