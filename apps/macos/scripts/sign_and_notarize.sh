#!/usr/bin/env bash
#
# sign_and_notarize.sh — Developer ID sign the app inside-out, then notarize and
# staple it (docs/desktop/release.md).
#
# The embedded Python runtime (Step 7.1) is added to Contents/Resources *after*
# Xcode signs the app, which invalidates the app seal — so the release pipeline
# re-signs everything here, from the innermost binaries outward:
#   1. every .so/.dylib in iris-runtime         (hardened runtime, no entitlements)
#   2. the embedded python interpreter           (hardened runtime + runtime entitlements)
#   3. embedded frameworks (IrisKit.framework)   (hardened runtime)
#   4. the app bundle itself                      (hardened runtime + app entitlements)
# then verifies, notarizes a zip via notarytool, and staples the .app so first
# launch is Gatekeeper-clean offline.
#
# Usage:
#   sign_and_notarize.sh /path/to/Iris.app
#
# Required environment:
#   DEVELOPER_ID_APP   "Developer ID Application: Name (TEAMID)" (codesign identity)
# Notarization credentials (App Store Connect API key):
#   NOTARY_KEY_PATH    path to the AuthKey_XXXX.p8
#   NOTARY_KEY_ID      the key ID
#   NOTARY_ISSUER_ID   the issuer UUID
# Optional:
#   ENTITLEMENTS_APP       (default: apps/macos/Iris/Iris.entitlements)
#   ENTITLEMENTS_RUNTIME   (default: apps/macos/scripts/iris-runtime.entitlements)
#   SKIP_NOTARIZE=1        sign + verify only (local dry runs)
set -euo pipefail

log() { printf '[sign] %s\n' "$*" >&2; }
die() { log "error: $*"; exit 1; }

APP="${1:-}"
[[ -n "${APP}" && -d "${APP}" ]] || die "usage: sign_and_notarize.sh /path/to/Iris.app"
APP="$(cd "${APP}" && pwd)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENTITLEMENTS_APP="${ENTITLEMENTS_APP:-${MACOS_DIR}/Iris/Iris.entitlements}"
ENTITLEMENTS_RUNTIME="${ENTITLEMENTS_RUNTIME:-${SCRIPT_DIR}/iris-runtime.entitlements}"
IDENTITY="${DEVELOPER_ID_APP:-}"

[[ -n "${IDENTITY}" ]] || die "DEVELOPER_ID_APP is required"
[[ -f "${ENTITLEMENTS_APP}" ]] || die "app entitlements not found: ${ENTITLEMENTS_APP}"
[[ -f "${ENTITLEMENTS_RUNTIME}" ]] || die "runtime entitlements not found: ${ENTITLEMENTS_RUNTIME}"

RUNTIME="${APP}/Contents/Resources/iris-runtime"
[[ -d "${RUNTIME}" ]] || die "embedded runtime missing (run a Release build first): ${RUNTIME}"

# Secure timestamps require a real identity; ad-hoc ("-") is for local dry runs.
TS="--timestamp"
[[ "${IDENTITY}" == "-" ]] && TS="--timestamp=none"

sign() { codesign --force "${TS}" --options runtime --sign "${IDENTITY}" "$@"; }
is_macho() { lipo -info "$1" >/dev/null 2>&1; }

# --- 1. nested native libraries (deepest first) -----------------------------
log "signing embedded native libraries (.so/.dylib)"
# NUL-delimited to survive any odd paths; sign one at a time.
while IFS= read -r -d '' lib; do
  sign "${lib}"
done < <(find "${RUNTIME}" -type f \( -name "*.so" -o -name "*.dylib" \) -print0)

# --- 2. the embedded interpreter(s), with runtime entitlements --------------
log "signing the embedded python interpreter"
while IFS= read -r -d '' bin; do
  is_macho "${bin}" || continue   # skip the *-config shell/python scripts
  codesign --force "${TS}" --options runtime \
    --entitlements "${ENTITLEMENTS_RUNTIME}" --sign "${IDENTITY}" "${bin}"
done < <(find "${RUNTIME}/python/bin" -type f -print0)

# --- 3. embedded frameworks --------------------------------------------------
if [[ -d "${APP}/Contents/Frameworks" ]]; then
  for fw in "${APP}/Contents/Frameworks"/*; do
    [[ -e "${fw}" ]] || continue
    log "signing $(basename "${fw}")"
    sign "${fw}"
  done
fi

# --- 4. the app bundle (outermost), with app entitlements -------------------
log "signing the app bundle"
codesign --force "${TS}" --options runtime \
  --entitlements "${ENTITLEMENTS_APP}" --sign "${IDENTITY}" "${APP}"

# --- verify the static signature --------------------------------------------
log "verifying code signature"
codesign --verify --strict --verbose=2 "${APP}" || die "codesign verify failed"

# --- 5. notarize + staple ----------------------------------------------------
if [[ "${SKIP_NOTARIZE:-0}" == "1" ]]; then
  log "SKIP_NOTARIZE=1 — signed and verified, skipping notarization"
  exit 0
fi
: "${NOTARY_KEY_PATH:?NOTARY_KEY_PATH is required}"
: "${NOTARY_KEY_ID:?NOTARY_KEY_ID is required}"
: "${NOTARY_ISSUER_ID:?NOTARY_ISSUER_ID is required}"

ZIP="$(mktemp -d)/Iris.zip"
log "zipping app for notarization"
/usr/bin/ditto -c -k --keepParent "${APP}" "${ZIP}"

log "submitting to the notary service (waits for the result)"
xcrun notarytool submit "${ZIP}" \
  --key "${NOTARY_KEY_PATH}" --key-id "${NOTARY_KEY_ID}" --issuer "${NOTARY_ISSUER_ID}" \
  --wait || die "notarization failed (see notarytool log)"

log "stapling the notarization ticket"
xcrun stapler staple "${APP}" || die "stapling failed"

log "final Gatekeeper assessment"
spctl --assess --type execute --verbose=4 "${APP}" || die "spctl assessment failed"

log "done: ${APP} is signed, notarized, and stapled"
