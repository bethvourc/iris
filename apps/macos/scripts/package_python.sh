#!/usr/bin/env bash
#
# package_python.sh — build the relocatable embedded Python runtime that ships
# inside the app bundle, so Iris runs the daemon on a clean Mac with no system
# Python and no dev tools (docs/desktop/packaging.md).
#
# Supports universal (arm64 + x86_64) builds: each architecture is staged from a
# pinned python-build-standalone interpreter plus uv.lock-pinned dependencies,
# then the Mach-O binaries are merged with `lipo` into a single fat runtime.
#
# Layout produced:
#   <dest>/iris-runtime/
#     VERSION                      # provenance: pins + arches + build time
#     python/bin/python3           # interpreter (fat if universal)
#     python/lib/python3.x/site-packages/iris/   # the daemon + locked deps
#
# Reproducible: the interpreter is a pinned python-build-standalone release and
# every dependency is resolved from the committed uv.lock.
#
# Usage:
#   package_python.sh [DEST_RESOURCES_DIR]
# Architectures come from Xcode's $ARCHS (default "arm64 x86_64"). The script
# no-ops for Debug builds (dev mode runs from the repo via uv instead).
set -euo pipefail

# --- pinned versions (bump together; see packaging.md "Updating") -----------
PYTHON_VERSION="3.12.8"
PYMINOR="${PYTHON_VERSION%.*}"          # e.g. 3.12
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

if [[ $# -ge 1 ]]; then
  RESOURCES_DIR="$1"
elif [[ -n "${BUILT_PRODUCTS_DIR:-}" && -n "${UNLOCALIZED_RESOURCES_FOLDER_PATH:-}" ]]; then
  RESOURCES_DIR="${BUILT_PRODUCTS_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}"
else
  die "no destination given and not running under Xcode (pass a Resources dir)"
fi
RUNTIME_DIR="${RESOURCES_DIR}/iris-runtime"

# --- architectures (universal by default) -----------------------------------
read -ra ARCH_LIST <<< "${ARCHS:-arm64 x86_64}"
[[ ${#ARCH_LIST[@]} -ge 1 ]] || die "no architectures to build"

pbs_arch()  { case "$1" in arm64|aarch64) echo aarch64-apple-darwin;; x86_64) echo x86_64-apple-darwin;; *) die "unsupported arch: $1";; esac; }
uv_plat()   { case "$1" in arm64|aarch64) echo aarch64-apple-darwin;; x86_64) echo x86_64-apple-darwin;; *) die "unsupported arch: $1";; esac; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }
require curl; require tar; require lipo
UV_BIN="${UV:-$(command -v uv || true)}"
[[ -n "${UV_BIN}" ]] || die "uv not found; install from https://docs.astral.sh/uv/"

CACHE_DIR="${IRIS_PBS_CACHE:-${HOME}/Library/Caches/Iris/python-build-standalone}"
mkdir -p "${CACHE_DIR}"

# --- export locked requirements once (shared across arches) -----------------
TMP_REQ="$(mktemp -t iris-reqs.XXXXXX).txt"
STAGING="$(mktemp -d -t iris-staging.XXXXXX)"
trap 'rm -f "${TMP_REQ}"; rm -rf "${STAGING}"' EXIT
EXPORT_ARGS=(export --frozen --no-dev --no-emit-project --format requirements-txt
  --project "${REPO_ROOT}" -o "${TMP_REQ}")
[[ "${BUNDLE_WAKEWORD}" == "1" ]] && { EXPORT_ARGS+=(--extra wakeword); log "including the wakeword extra"; }
log "exporting locked requirements from uv.lock"
"${UV_BIN}" "${EXPORT_ARGS[@]}" >/dev/null || die "uv export failed (is uv.lock committed?)"

# --- build one self-contained runtime tree per architecture -----------------
build_arch() {
  local arch="$1" parch tree site asset url tarball
  parch="$(pbs_arch "$arch")"
  tree="${STAGING}/${arch}"
  site="${tree}/python/lib/python${PYMINOR}/site-packages"

  asset="cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${parch}-install_only.tar.gz"
  url="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${asset}"
  tarball="${CACHE_DIR}/${asset}"
  if [[ ! -f "${tarball}" ]]; then
    log "[${arch}] downloading ${asset}"
    curl -fSL --retry 3 -o "${tarball}.tmp" "${url}" || die "download failed: ${url}"
    mv "${tarball}.tmp" "${tarball}"
  else
    log "[${arch}] using cached interpreter"
  fi

  mkdir -p "${tree}"
  tar -xzf "${tarball}" -C "${tree}"   # extracts a top-level python/
  [[ -d "${site}" ]] || die "[${arch}] site-packages missing after extract"

  # Install deps for this arch's wheel tags (no interpreter executed — uv selects
  # wheels by --python-platform/--python-version and installs into --target).
  log "[${arch}] installing locked dependencies"
  "${UV_BIN}" pip install --quiet --no-cache --require-hashes \
    --python-platform "$(uv_plat "$arch")" --python-version "${PYMINOR}" \
    --target "${site}" -r "${TMP_REQ}" || die "[${arch}] dependency install failed"

  # The daemon itself is pure Python (py3-none-any).
  log "[${arch}] installing the iris package"
  "${UV_BIN}" pip install --quiet --no-cache --no-deps \
    --python-platform "$(uv_plat "$arch")" --python-version "${PYMINOR}" \
    --target "${site}" "${REPO_ROOT}" || die "[${arch}] iris install failed"

  prune_tree "${tree}/python"
}

# Drop GUI/test/build cruft we never ship.
prune_tree() {
  local root="$1"
  find "${root}/lib" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
  local mod
  for mod in tkinter turtledemo idlelib ensurepip lib2to3; do
    rm -rf "${root}"/lib/python*/"${mod}" 2>/dev/null || true
  done
  rm -rf "${root}"/lib/python*/test 2>/dev/null || true
}

# --- merge a secondary arch tree onto the (primary) output via lipo ---------
is_macho() { lipo -info "$1" >/dev/null 2>&1; }

# Combine two Mach-O files into one fat binary, deduping shared slices. Written
# for macOS's system bash 3.2 (no associative arrays).
merge_macho() {
  local dest="$1" other="$2" sd mode seen="" input archs n a
  local order=()
  sd="$(mktemp -d)"; mode="$(stat -f '%Lp' "${dest}")"
  for input in "${dest}" "${other}"; do
    archs="$(lipo -archs "${input}" 2>/dev/null)" || continue
    n="$(wc -w <<<"${archs}")"
    for a in ${archs}; do
      case " ${seen} " in *" ${a} "*) continue ;; esac
      if [[ "${n}" -eq 1 ]]; then cp "${input}" "${sd}/${a}"
      else lipo "${input}" -thin "${a}" -output "${sd}/${a}" 2>/dev/null || continue; fi
      seen="${seen} ${a}"; order+=("${sd}/${a}")
    done
  done
  if (( ${#order[@]} > 1 )); then
    lipo -create "${order[@]}" -output "${dest}" 2>/dev/null || true
    chmod "${mode}" "${dest}"
  fi
  rm -rf "${sd}"
}

merge_tree() {
  local src="$1" dest="$2" f rel d
  while IFS= read -r -d '' f; do
    rel="${f#"${src}"/}"; d="${dest}/${rel}"
    [[ -L "${f}" ]] && continue                # symlinks already in primary
    if [[ ! -e "${d}" ]]; then
      mkdir -p "$(dirname "${d}")"; cp -p "${f}" "${d}"; continue
    fi
    if is_macho "${f}" && is_macho "${d}"; then merge_macho "${d}" "${f}"; fi
    # else: identical pure-Python/text — keep the primary copy
  done < <(find "${src}" -type f -print0)
}

# --- run the per-arch builds, then assemble the final runtime ----------------
for arch in "${ARCH_LIST[@]}"; do build_arch "${arch}"; done

primary="${ARCH_LIST[0]}"
log "assembling runtime (primary: ${primary}; arches: ${ARCH_LIST[*]})"
rm -rf "${RUNTIME_DIR}"; mkdir -p "${RUNTIME_DIR}"
cp -R "${STAGING}/${primary}/python" "${RUNTIME_DIR}/python"
for arch in "${ARCH_LIST[@]:1}"; do
  log "merging ${arch} slices with lipo"
  merge_tree "${STAGING}/${arch}/python" "${RUNTIME_DIR}/python"
done

PY="${RUNTIME_DIR}/python/bin/python3"
[[ -x "${PY}" ]] || die "interpreter missing after assembly: ${PY}"

# --- provenance + sanity check ----------------------------------------------
cat > "${RUNTIME_DIR}/VERSION" <<EOF
python=${PYTHON_VERSION}
python_build_standalone=${PBS_RELEASE}
arches=${ARCH_LIST[*]}
interpreter_archs=$(lipo -archs "${PY}" 2>/dev/null || echo unknown)
wakeword=${BUNDLE_WAKEWORD}
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

# The merged interpreter contains the host slice, so it runs natively here.
log "verifying the runtime can import the daemon"
"${PY}" -c "import iris.cli, iris.gateway; print('iris runtime ok')" \
  || die "sanity import failed"

log "done: ${RUNTIME_DIR} ($(lipo -archs "${PY}" 2>/dev/null))"
