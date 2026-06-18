#!/usr/bin/env bash
#
# make_dmg.sh — package the signed, notarized, stapled Iris.app into a
# drag-to-Applications DMG, sign the DMG, and print its SHA-256.
#
# The app inside is already notarized and stapled by sign_and_notarize.sh, so a
# signed DMG containing it is Gatekeeper-clean on first launch (offline). Set
# NOTARIZE_DMG=1 to additionally notarize + staple the DMG itself.
#
# Usage:
#   make_dmg.sh /path/to/Iris.app [output.dmg]
#
# Required environment:
#   DEVELOPER_ID_APP   "Developer ID Application: Name (TEAMID)" (codesign identity)
# Optional:
#   NOTARIZE_DMG=1     also notarize + staple the DMG (needs NOTARY_* vars)
set -euo pipefail

log() { printf '[dmg] %s\n' "$*" >&2; }
die() { log "error: $*"; exit 1; }

APP="${1:-}"
[[ -n "${APP}" && -d "${APP}" ]] || die "usage: make_dmg.sh /path/to/Iris.app [output.dmg]"
APP="$(cd "${APP}" && pwd)"
IDENTITY="${DEVELOPER_ID_APP:-}"
[[ -n "${IDENTITY}" ]] || die "DEVELOPER_ID_APP is required"
# Secure timestamps require a real identity; ad-hoc ("-") is for local dry runs.
TS="--timestamp"
[[ "${IDENTITY}" == "-" ]] && TS="--timestamp=none"

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
  "${APP}/Contents/Info.plist" 2>/dev/null || echo "0.0.0")"
DMG="${2:-$(pwd)/dist/Iris-${VERSION}.dmg}"
mkdir -p "$(dirname "${DMG}")"
rm -f "${DMG}"

VOLNAME="Iris ${VERSION}"

if command -v create-dmg >/dev/null 2>&1; then
  log "building DMG with create-dmg"
  # create-dmg exits non-zero on a benign AppleScript timeout in headless CI;
  # treat a produced file as success.
  create-dmg \
    --volname "${VOLNAME}" \
    --window-pos 200 120 \
    --window-size 540 380 \
    --icon-size 100 \
    --icon "Iris.app" 150 190 \
    --app-drop-link 390 190 \
    --hide-extension "Iris.app" \
    --no-internet-enable \
    "${DMG}" "${APP}" || true
  [[ -f "${DMG}" ]] || die "create-dmg did not produce ${DMG}"
else
  log "create-dmg not found — using hdiutil fallback"
  STAGE="$(mktemp -d)"
  cp -R "${APP}" "${STAGE}/"
  ln -s /Applications "${STAGE}/Applications"
  hdiutil create -volname "${VOLNAME}" -srcfolder "${STAGE}" \
    -fs HFS+ -format UDZO -ov "${DMG}" >/dev/null || die "hdiutil create failed"
  rm -rf "${STAGE}"
fi

log "signing the DMG"
codesign --force "${TS}" --sign "${IDENTITY}" "${DMG}" || die "DMG signing failed"

if [[ "${NOTARIZE_DMG:-0}" == "1" ]]; then
  : "${NOTARY_KEY_PATH:?}"; : "${NOTARY_KEY_ID:?}"; : "${NOTARY_ISSUER_ID:?}"
  log "notarizing the DMG"
  xcrun notarytool submit "${DMG}" \
    --key "${NOTARY_KEY_PATH}" --key-id "${NOTARY_KEY_ID}" \
    --issuer "${NOTARY_ISSUER_ID}" --wait || die "DMG notarization failed"
  xcrun stapler staple "${DMG}" || die "DMG stapling failed"
fi

SHA="$(shasum -a 256 "${DMG}" | awk '{print $1}')"
log "done: ${DMG}"
log "sha256: ${SHA}"
# Emit machine-readable lines for the release pipeline to capture.
echo "DMG_PATH=${DMG}"
echo "DMG_SHA256=${SHA}"
echo "DMG_VERSION=${VERSION}"
