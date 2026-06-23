# Production Readiness Review (Step 7.5)

A four-perspective review of the release build — **User**, **Operator**,
**Security**, **Maintainer**. The bar is "would a strong team ship this," not
"does it compile." Findings are logged here with resolutions; the step closes
when every suite is green and this doc has zero open findings.

- **Status legend:** ✅ verified · ⚠️ verified with caveat · 🔲 manual, pending a signed release build
- **Last automated run:** 2026-06-20

---

## 1. Maintainer — suites, contracts, dead code

| Gate | Command | Result |
|------|---------|--------|
| Python tests | `uv run pytest` | ✅ 266 passed |
| Python lint | `uv run ruff check .` | ✅ clean |
| Swift unit tests | `xcodebuild test -scheme Iris -only-testing:IrisTests` | ✅ 158 passed |
| Swift UI tests | `xcodebuild test -scheme IrisUITests -only-testing:IrisUITests/ResilienceUITests` | ✅ 12 passed |
| Swift lint | `swiftlint lint` (Iris, IrisKit, IrisTests, IrisUITests) | ✅ clean |
| Swift format | `swiftformat --lint` (same dirs) | ✅ 0/74 require formatting |

> No separate type checker is configured (`pyproject.toml` dev deps: `pytest`,
> `ruff`); `ruff` + `pytest` are the Python gates. `swiftformat --lint .` reports
> issues only inside `build/xcode-derived/SourcePackages/` (the third-party
> KeyboardShortcuts checkout) — excluded from our scope.

- **Contract-doc accuracy:** the routes in `api-contract.md` (`/voice/*`,
  `/activity`, `/settings`, `/secrets`, extended `/health`) match the gateway's
  dispatch table; the auth-exempt set matches `AUTH_EXEMPT_PATHS`. The full route
  list is now pinned by the deny-by-default scan (§3), so contract drift fails a
  test.
- **Dead-code sweep:** spot sweep of the desktop surfaces found no orphaned
  types or unused `--ui-test-*` hooks; the scripted providers are all referenced
  by `AppModel`. (Full cross-module sweep is part of the maintainer sign-off
  against a release build.)

**Open findings:** none.

---

## 2. Security

| Check | Status | Evidence |
|-------|--------|----------|
| Binds `127.0.0.1` only (no `0.0.0.0`) | ✅ | `gateway.serve(host="127.0.0.1")`, `cli.py --host` default `127.0.0.1`; no `0.0.0.0` anywhere |
| Auth required on every non-exempt route | ✅ | Deny-by-default scan: `test_every_protected_route_denies_by_default`, `test_auth_exempt_set_is_locked`, `test_no_protected_route_is_silently_exempt` |
| Constant-time token comparison | ✅ | `authorize()` uses `hmac.compare_digest` |
| Token never written to disk | ✅ | Held in Keychain; injected via `IRIS_GATEWAY_TOKEN` env only (`DaemonManager.launchProcess`) |
| Secrets absent from logs | ✅ | `test_requests_are_logged_without_leaking_tokens`; request logger records path/status/duration, never bodies/tokens |
| Secrets never serialize into API/UI | ✅ | `test_secret_values_never_serialize`; settings/secrets report `is_set` only |
| Secrets absent from diagnostics zip | ✅ | `DiagnosticsTests.testRedactScrubsKnownKeyShapes`, `testBundleContainsNoSecrets`; every file redacted before write |
| Settings whitelist enforced | ✅ | `SETTING_SPECS` whitelist; `test_put_unknown_key_is_rejected_with_envelope`, `test_put_env_managed_key_returns_409` |
| Least-privilege entitlements | ✅ | App: microphone only (`device.audio-input`). Hardened-runtime exceptions scoped to the embedded interpreter (`scripts/iris-runtime.entitlements`), not the app |
| Signed binaries (`codesign --verify --deep`) | 🔲 | Requires a signed release artifact — run during the release/notarization pass (7.2) |

**Hardening added this pass:** the deny-by-default route scan (`tests/test_gateway.py`)
locks `AUTH_EXEMPT_PATHS` to `{/health, /dashboard, /memory-browser}` and asserts
every other route rejects missing/wrong tokens — so a future route that bypasses
auth, or an accidental exempt-set widening, fails CI.

**Open findings:** none automatable-side. `codesign --verify --deep --strict`
and a `spctl --assess` gatekeeper check remain for the signed build.

---

## 3. User — full journey (manual, signed build)

Run on a clean account; note every rough edge against the design standard (flat
surfaces, hairline borders, one accent, no gradients/glows/toasts). Capture a
screenshot of each surface into `docs/desktop/screenshots/readiness/`.

1. **Install** — mount DMG, drag to Applications, first launch (Gatekeeper
   prompt is clean). 🔲
2. **Onboard** — welcome → API key → microphone → system access → done; denied
   permissions stay skippable (degraded, never a dead end). 🔲
3. **First session** — hotkey opens the overlay; listening → thinking →
   speaking render; transcript readable. 🔲
4. **Interrupt** — barge-in stops playback instantly. 🔲
5. **Review activity** — main window activity feed populates; a detail opens. 🔲
6. **Change settings** — voice/port/launch-at-login persist across relaunch. 🔲
7. **Quit / relaunch** — clean shutdown; daemon adopts or restarts; no orphan
   process, no stale overlay. 🔲

(The UI destinations for failure states are already covered by
`ResilienceUITests`; this pass is the happy-path journey on a real build.)

---

## 4. Operator — diagnose three induced faults (manual, signed build)

Using **only** `daemon.log` + the [runbook](runbook.md) (no source), diagnose and
recover from three induced faults. Confirms the logs + runbook are sufficient.

1. **Port conflict** — occupy `8765`, launch; expect to find `gateway.port_in_use`
   in the log and recover via runbook §5. 🔲
2. **Token mismatch** — delete the Keychain item while running; expect 401s,
   diagnose via the runbook §7 table, recover via §4a. 🔲
3. **Corrupt settings** — write garbage `settings.json`; expect
   `settings.corrupt_file_ignored`, confirm graceful defaults, rewrite via §4b. 🔲

(The daemon-side behavior of all three is already verified by
`apps/macos/scripts/resilience_drill.py`; this pass tests the human + docs loop.)

---

## 5. Sign-off checklist

- [x] Maintainer suites green (pytest, IrisTests, ResilienceUITests, ruff, swiftlint, swiftformat)
- [x] Security automated checks pass; deny-by-default auth scan added
- [ ] `codesign --verify --deep --strict` + `spctl --assess` on the signed DMG
- [ ] User journey walked on a clean account; screenshots archived
- [ ] Operator diagnosed all three faults from logs + runbook alone
- [ ] Final screenshot set of every surface/state archived in the repo

**This step stays open until the four boxes above are checked against a signed
release build.**
