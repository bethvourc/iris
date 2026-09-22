# Release: signing, notarization, and distribution

Iris ships as a Developer ID–signed, notarized, **universal** `.app` inside a
drag-to-Applications DMG, so it launches Gatekeeper-clean on a clean Mac with no
warnings. The whole pipeline is automated by
[`.github/workflows/release.yml`](../../.github/workflows/release.yml), triggered
by pushing a `v*` tag. This document covers the one-time setup, the release and
rollback procedures, and how to verify the result.

Related: [packaging.md](packaging.md) (the embedded Python runtime that this
pipeline signs).

## What the pipeline does

On a `v*` tag (or a manual `workflow_dispatch`), on a macOS runner:

1. Install `uv`, `xcodegen`, `create-dmg`.
2. Import the Developer ID Application certificate into a temporary keychain.
3. `xcodegen generate`, then build a **universal** Release **unsigned**
   (`CODE_SIGNING_ALLOWED=NO`, `ONLY_ACTIVE_ARCH=NO`). The post-build phase
   embeds the Python runtime into `Contents/Resources/iris-runtime`.
4. `scripts/sign_and_notarize.sh` — sign **inside-out** (every runtime
   `.so`/`.dylib`, then the interpreter with runtime entitlements, then
   `IrisKit.framework`, then the app with the app entitlements), verify,
   `notarytool submit --wait`, and `stapler staple` the app.
5. `scripts/make_dmg.sh` — build the drag-to-Applications DMG, sign it, print its
   SHA-256.
6. On a tag, publish a **draft** GitHub Release with the DMG and a `.sha256`
   file. An untagged `workflow_dispatch` run uploads the same two files as a
   workflow **artifact** instead — a GitHub Release requires a tag.

Signing is done by our scripts (not Xcode) because the embedded runtime is added
*after* Xcode would sign — which invalidates the seal. Re-signing inside-out is
the only correct order.

### Why two entitlement sets

- **App** (`Iris/Iris.entitlements`): microphone only. No App Sandbox (Iris
  spawns the daemon and uses Accessibility control).
- **Embedded interpreter** (`scripts/iris-runtime.entitlements`): hardened-runtime
  exceptions a bundled CPython needs to load third-party native wheels —
  `allow-unsigned-executable-memory` (cffi/ctypes), `allow-jit`, and
  `disable-library-validation`. These apply only to the interpreter binary, never
  the app.

## One-time setup: required secrets

Create these as repository encrypted secrets (Settings → Secrets and variables →
Actions):

| Secret | What it is |
| --- | --- |
| `DEVELOPER_ID_APP_CERT_P12_BASE64` | `base64` of the exported **Developer ID Application** certificate + private key (`.p12`) |
| `DEVELOPER_ID_APP_CERT_PASSWORD` | password set when exporting the `.p12` |
| `DEVELOPER_ID_APP_IDENTITY` | the codesign identity string, e.g. `Developer ID Application: Your Name (TEAMID)` |
| `NOTARY_KEY_P8_BASE64` | `base64` of the App Store Connect API key (`AuthKey_XXXX.p8`) |
| `NOTARY_KEY_ID` | the API key ID |
| `NOTARY_ISSUER_ID` | the App Store Connect issuer UUID |

Obtaining them:

- **Developer ID certificate** — in the Apple Developer portal create a
  *Developer ID Application* certificate, install it in Keychain Access, then
  export the certificate **with its private key** as a `.p12`. Encode it:
  `base64 -i DeveloperID.p12 | pbcopy`. Find the identity string with
  `security find-identity -v -p codesigning`.
- **Notary API key** — App Store Connect → Users and Access → Integrations →
  App Store Connect API → generate a key with the *Developer* role. Download the
  `.p8` (once only), note its Key ID and the Issuer ID. Encode:
  `base64 -i AuthKey_XXXX.p8 | pbcopy`.

API-key notarization is preferred over an Apple ID + app-specific password: no
account password in CI and it doesn't break on 2FA.

## Cutting a release

1. Bump the version: `MARKETING_VERSION` (and `CURRENT_PROJECT_VERSION`) in
   `apps/macos/project.yml`; run `xcodegen generate` and commit.
2. Tag and push:
   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. The workflow runs, producing a **draft** release with the DMG + `.sha256`.
4. Download the DMG, smoke-test it on a clean Mac (see *Verifying* below), then
   publish the draft.

Use `workflow_dispatch` to dry-run the full build/sign/notarize path without
tagging. No release is created — download the DMG from the run's **Artifacts**.
Note this still spends a real notarization submission.

## Verifying (clean Mac)

```sh
# Static signature + hardened runtime
codesign --verify --deep --strict --verbose=2 /Applications/Iris.app

# Gatekeeper acceptance (must say "accepted" / "source=Notarized Developer ID")
spctl --assess --type execute --verbose=4 /Applications/Iris.app

# The notarization ticket is stapled (works offline)
stapler validate /Applications/Iris.app

# The embedded interpreter is signed with the runtime entitlements
codesign -d --entitlements - --xml \
  /Applications/Iris.app/Contents/Resources/iris-runtime/python/bin/python3.12
```

The real acceptance test is launching on a Mac that has never seen the app and
has no developer tools: it opens without a Gatekeeper prompt, the menu bar
reaches **healthy**, and a voice session works.

## Rollback

Releases are immutable artifacts, so rollback is "re-point users at the previous
one":

- The previous DMG remains attached to its GitHub Release — no rebuild needed.
- If a bad release was published, mark it **pre-release** or delete it; the prior
  release becomes the latest download.
- To ship a fix, cut a new patch tag (`v0.1.1`). Never move an existing tag.
- Checksums in each release's notes let users verify what they downloaded.

## Rotating the signing identity

When the Developer ID certificate is renewed or compromised:

1. Create/download the new Developer ID Application certificate and `.p12`.
2. Update `DEVELOPER_ID_APP_CERT_P12_BASE64`, `DEVELOPER_ID_APP_CERT_PASSWORD`,
   and `DEVELOPER_ID_APP_IDENTITY` secrets.
3. If a certificate was revoked, previously notarized builds keep working
   (notarization tickets are independent of cert validity), but cut a fresh
   release signed with the new identity going forward.

Rotate the notary API key the same way by regenerating it in App Store Connect
and updating the `NOTARY_*` secrets.

## Local dry run (no Developer ID)

Both scripts accept the ad-hoc identity `-` and `SKIP_NOTARIZE=1` to validate the
signing/DMG logic without real credentials:

```sh
cd apps/macos
xcodebuild -scheme Iris -configuration Release -derivedDataPath build \
  ONLY_ACTIVE_ARCH=NO build
APP=build/Build/Products/Release/Iris.app
DEVELOPER_ID_APP="-" SKIP_NOTARIZE=1 ./scripts/sign_and_notarize.sh "$APP"
DEVELOPER_ID_APP="-" ./scripts/make_dmg.sh "$APP" /tmp/Iris-dryrun.dmg
```

Ad-hoc signatures are not Gatekeeper-valid — this only exercises the scripts.
