#!/usr/bin/env bash
#
# package_python.sh — build the relocatable embedded Python runtime that ships
# inside the app bundle, so Iris runs the daemon on a clean Mac with no system
# Python and no dev tools (docs/desktop/packaging.md).
#
# Layout produced:
#   <dest>/iris-runtime/
#     VERSION                      # provenance: pins + build time
#     python/bin/python3           # python-build-standalone interpreter
#     python/lib/python3.x/site-packages/iris/   # the daemon + locked deps
#
# Reproducible: the interpreter is a pinned python-build-standalone release and
# every dependency is resolved from the committed uv.lock. Re-running with the
# same pins and lock produces the same tree.
#
# Usage:
#   package_python.sh [DEST_RESOURCES_DIR]
# When invoked from the Xcode "Embed Python runtime" build phase, the
# destination and architecture are read from the build environment and the
# script no-ops for Debug builds (dev mode runs from the repo via uv instead).
set -euo pipefail

# --- pinned versions (bump together; see packaging.md "Updating") -----------
PYTHON_VERSION="3.12.8"
PBS_RELEASE="20241219"   # astral-sh/python-build-standalone release tag
# Bundle the on-device wake-word stack (onnxruntime/openwakeword/numpy). Off by
# default to keep the bundle lean; the feature is opt-in and imports lazily.
BUNDLE_WAKEWORD="${IRIS_BUNDLE_WAKEWORD:-0}"

log() { printf '[package_python] %s\n' "$*" >&2; }
die() { log "error: $*"; exit 1; }

# --- skip for Debug: dev mode runs from the repo checkout via uv -------------
if [[ "${CONFIGURATION:-Release}" == "Debug" ]]; then
  log "Debug configuration — skipping embedded runtime (dev mode uses the repo)."
  exit 0
fi

# --- resolve repo root and destination --------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
[[ -f "${REPO_ROOT}/pyproject.toml" ]] || die "repo root not found at ${REPO_ROOT}"

# Destination: explicit arg, else the app bundle's Resources from Xcode env.
if [[ $# -ge 1 ]]; then
  RESOURCES_DIR="$1"
elif [[ -n "${BUILT_PRODUCTS_DIR:-}" && -n "${UNLOCALIZED_RESOURCES_FOLDER_PATH:-}" ]]; then
  RESOURCES_DIR="${BUILT_PRODUCTS_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}"
else
  die "no destination given and not running under Xcode (pass a Resources dir)"
fi
RUNTIME_DIR="${RESOURCES_DIR}/iris-runtime"

# --- choose the python-build-standalone asset for the target arch -----------
# Prefer Xcode's $ARCHS (single arch per pass); fall back to the host. PBS ships
# per-arch archives — universal builds would lipo two trees (out of scope here).
ARCH="${ARCHS:-$(uname -m)}"
ARCH="${ARCH%% *}"   # first token if Xcode passes multiple
case "${ARCH}" in
  arm64|aarch64) PBS_ARCH="aarch64-apple-darwin" ;;
  x86_64)        PBS_ARCH="x86_64-apple-darwin" ;;
  *) die "unsupported architecture: ${ARCH}" ;;
esac

ASSET="cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${PBS_ARCH}-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${ASSET}"

# Cache downloads so repeat builds don't re-fetch ~30MB.
CACHE_DIR="${IRIS_PBS_CACHE:-${HOME}/Library/Caches/Iris/python-build-standalone}"
mkdir -p "${CACHE_DIR}"
TARBALL="${CACHE_DIR}/${ASSET}"

require() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }
require curl
require tar
UV_BIN="${UV:-$(command -v uv || true)}"
[[ -n "${UV_BIN}" ]] || die "uv not found; install from https://docs.astral.sh/uv/"

# --- 1. fetch the interpreter ------------------------------------------------
if [[ ! -f "${TARBALL}" ]]; then
  log "downloading ${ASSET}"
  curl -fSL --retry 3 -o "${TARBALL}.tmp" "${URL}" || die "download failed: ${URL}"
  mv "${TARBALL}.tmp" "${TARBALL}"
else
  log "using cached ${ASSET}"
fi

# --- 2. lay down a clean runtime tree ---------------------------------------
log "extracting interpreter into ${RUNTIME_DIR}"
rm -rf "${RUNTIME_DIR}"
mkdir -p "${RUNTIME_DIR}"
# PBS install_only archives extract to a top-level "python/" directory.
tar -xzf "${TARBALL}" -C "${RUNTIME_DIR}"
PY="${RUNTIME_DIR}/python/bin/python3"
[[ -x "${PY}" ]] || die "interpreter missing after extract: ${PY}"

# --- 3. resolve locked dependencies into the runtime ------------------------
TMP_REQ="$(mktemp -t iris-reqs.XXXXXX).txt"
trap 'rm -f "${TMP_REQ}"' EXIT
EXPORT_ARGS=(export --frozen --no-dev --no-emit-project --format requirements-txt
  --project "${REPO_ROOT}" -o "${TMP_REQ}")
if [[ "${BUNDLE_WAKEWORD}" == "1" ]]; then
  EXPORT_ARGS+=(--extra wakeword)
  log "including the wakeword extra"
fi
log "exporting locked requirements from uv.lock"
"${UV_BIN}" "${EXPORT_ARGS[@]}" || die "uv export failed (is uv.lock committed?)"

log "installing dependencies into the runtime"
"${UV_BIN}" pip install --python "${PY}" --no-cache --require-hashes \
  -r "${TMP_REQ}" || die "dependency install failed"

# Install the daemon itself, no deps (already pinned above).
log "installing the iris package"
"${UV_BIN}" pip install --python "${PY}" --no-cache --no-deps \
  "${REPO_ROOT}" || die "iris install failed"

# --- 4. slim the tree (no GUI/test/build cruft needs to ship) ---------------
log "pruning unused stdlib and caches"
find "${RUNTIME_DIR}/python/lib" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
for mod in tkinter turtledemo idlelib ensurepip lib2to3; do
  rm -rf "${RUNTIME_DIR}"/python/lib/python3.*/"${mod}" 2>/dev/null || true
done
rm -rf "${RUNTIME_DIR}"/python/lib/python3.*/test 2>/dev/null || true

# --- 5. provenance + sanity check -------------------------------------------
cat > "${RUNTIME_DIR}/VERSION" <<EOF
python=${PYTHON_VERSION}
python_build_standalone=${PBS_RELEASE}
arch=${PBS_ARCH}
wakeword=${BUNDLE_WAKEWORD}
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

log "verifying the runtime can import the daemon"
"${PY}" -c "import iris.cli, iris.gateway; print('iris runtime ok')" \
  || die "sanity import failed"

log "done: ${RUNTIME_DIR}"
