# Iris for macOS — System Architecture

Status: authoritative for the desktop implementation (see `implementation/desktop-macos-plan.md`).
Companion document: [`api-contract.md`](api-contract.md) — the frozen wire contract between the
Swift app and the Python daemon. If this document and the code disagree, fix one of them in the
same change.

## 1. Overview

Iris for macOS is a native Swift/SwiftUI shell around the existing Python core. The Python core
keeps all intelligence and state; the app provides presence (menu bar), activation (global hotkey,
later local wake word), a Siri-style non-activating overlay for live voice sessions, and a main
window for activity, approvals, and settings.

```
┌─────────────────────────────── Iris.app (Swift) ───────────────────────────────┐
│  MenuBarExtra      Overlay (NSPanel)      Main Window       Onboarding         │
│       │                  │                     │                 │              │
│       └──────────────┬───┴─────────┬───────────┴───────┬────────┘              │
│                      ▼             ▼                   ▼                       │
│                 IrisKit: APIClient · SSEClient · TokenStore · DaemonManager    │
└───────────────────────────────────┬────────────────────────────────────────────┘
                                    │  HTTP + SSE, 127.0.0.1 only, Bearer token
┌───────────────────────────────────▼────────────────────────────────────────────┐
│  iris daemon (Python) — `iris serve`                                          │
│  GatewayService (http.server, threading)                                        │
│   ├─ VoiceController ── RealtimeSpeechSession ──► OpenAI Realtime (wss)         │
│   ├─ RunOrchestrator ── agent runs, approvals                                   │
│   ├─ ActivityService ── sessions / runs / transcripts aggregation               │
│   ├─ SettingsStore  ── layered config (env > settings.json > defaults)          │
│   └─ SQLite state DB, audit log, memory graph                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Design rule: **the Swift app never touches Iris state directly.** No SQLite reads, no settings
file writes, no imports of Python logic. Everything goes through the gateway API. This keeps the
CLI, the web dashboard (`/dashboard`), and the desktop app as peer clients of one backend.

## 2. Components and responsibilities

### Python daemon (existing core, extended)

| Component | File | Responsibility |
|---|---|---|
| GatewayService | `src/iris/gateway.py` | HTTP/SSE API, auth, routing. Single API boundary. |
| VoiceController | `src/iris/voice_controller.py` (new) | Owns at most one live voice session; start/stop/interrupt/status; fans out voice events to subscribers. |
| RealtimeSpeechSession | `src/iris/voice.py` | OpenAI Realtime session: mic capture, wake gating, barge-in, meeting mode, tool dispatch. Emits `VoiceEvent`s through an `EventSink` (new) instead of printing. |
| ActivityService | `src/iris/activity.py` (new) | Day-grouped activity feed + stats over existing sessions/runs/audit. |
| SettingsStore | `src/iris/settings_store.py` (new) | Whitelisted, file-backed mutable settings beneath env overrides. |
| RunOrchestrator | `src/iris/runtime.py` | Agent runs, run events, approvals (existing, unchanged). |

The daemon runs `iris serve --json-logs --log-dir ~/Library/Logs/Iris` and is otherwise the
same process that serves the web dashboard today.

### Swift app

| Component | Responsibility |
|---|---|
| `IrisKit/APIClient` | Typed HTTP client; bearer auth; normalizes failures to `IrisAPIError`. |
| `IrisKit/SSEClient` | `AsyncSequence` over `URLSession.bytes`; reconnect with jittered backoff; heartbeat/staleness detection. |
| `IrisKit/TokenStore` | Keychain-backed gateway token (generated on first run, device-only). |
| `IrisKit/DaemonManager` | Spawns/supervises the daemon; health polling; restart with backoff; crash-loop detection; adopt-if-already-running. |
| MenuBar | Permanent presence; status icon; Talk to Iris / Open Iris / Pause / Quit. |
| Overlay | Non-activating `NSPanel`; renders the live voice session state machine. |
| Main window | Home (stats + recent), Activity feed, Approvals, Settings. |
| Onboarding | First-run: OpenAI key, mic permission, Accessibility/Screen Recording guidance. |

## 3. Process lifecycle

**Who spawns whom.** Iris.app owns the daemon as a child `Process` in release builds. The daemon
executable is the embedded runtime at `Iris.app/Contents/Resources/iris-runtime/`. In dev mode the
app resolves `uv run iris serve` against the repo checkout.

**Startup sequence.**
1. App launches (login item via `SMAppService`, or manually).
2. `DaemonManager` probes `GET /health` on the configured port.
   - Healthy daemon already running (e.g. developer terminal): **adopt** it — do not spawn a
     duplicate. Token must still validate; if it does not, surface a token-mismatch state.
   - No daemon: spawn with env `IRIS_GATEWAY_TOKEN` (from Keychain), `--json-logs`,
     `--log-dir`, port. Poll `/health` until ready (bounded, 15s).
3. Menu bar reflects `DaemonState`: `stopped → launching → healthy → unhealthy → restarting → crashLooping`.

**Supervision.** On unexpected daemon exit: restart with exponential backoff (1s, 2s, 4s … cap
30s). ≥3 crashes within 60s ⇒ `crashLooping`, a **terminal** state: stop retrying, surface in the
menu bar and overlay with "Open Diagnostics", retain the last stderr tail.

**Shutdown.** App quit sends SIGTERM to a daemon it spawned (adopted daemons are left running),
waits up to 5s for clean exit (daemon flushes state, closes the voice session), then SIGKILL. The
quit path must leave no orphan Python processes.

**Port policy.** Default `127.0.0.1:8765`. If occupied by something that is not a healthy Iris
daemon, fail with an explicit port-conflict state (no silent port hopping — the token and port are
a pair stored in app preferences).

## 4. Trust boundaries and security model

```
[macOS user session]
   │ TCC: microphone, accessibility, screen recording → granted to Iris.app bundle
   ▼
[Iris.app] ── Keychain (gateway token, device-only, non-synced)
   │ Bearer token over loopback HTTP (127.0.0.1 only; never 0.0.0.0)
   ▼
[iris daemon] ── state dir (SQLite, settings.json, audit log) · env (.env: OPENAI_API_KEY)
   │ HTTPS/WSS outbound only
   ▼
[OpenAI API / Realtime]   [optional: Google Vision, Pushover]
```

- **Gateway auth**: every route requires `Authorization: Bearer <token>` except
  `AUTH_EXEMPT_PATHS = {/health, /dashboard, /memory-browser}` (`gateway.py:36`). Comparison is
  `hmac.compare_digest`. All new desktop endpoints (voice, activity, settings) require auth.
- **Token custody**: generated by the app (256-bit random) on first run, stored only in Keychain,
  injected into the daemon via environment. Never written to disk, defaults, or logs by either
  process. Rotation is a first-class recovery action (Settings → Advanced).
- **Secrets**: `OPENAI_API_KEY` and friends live in the daemon's environment/`.env`. The settings
  API reports secrets as `is_set: true|false` only — values never serialize into any response,
  log line, or diagnostics bundle (enforced by tests).
- **TCC attribution**: because the daemon is a child of Iris.app, macOS attributes microphone /
  Accessibility / Screen Recording usage to the Iris.app bundle. One permission grant covers both
  processes. This is a load-bearing reason for the child-process model.
- **Request limits**: 1 MB body cap (`MAX_BODY_BYTES`), bounded query params on list endpoints.
- **No sandbox**: the app is Developer ID distributed with hardened runtime; App Sandbox is
  incompatible with daemon spawning and Mac control. Entitlements are least-privilege
  (microphone usage description; no network server entitlement needed — loopback client only).

## 5. Core user flows

### Hotkey voice session (v1 activation)
1. User presses ⌥Space (configurable). HotkeyController asks VoiceSessionViewModel to toggle.
2. `POST /voice/start {"mode": "conversation"}` → 202 with session descriptor; overlay appears
   in `connecting`.
3. App consumes `GET /voice/events` (SSE). First frame is always a `state` snapshot; subsequent
   frames stream the session (see event catalog in the API contract).
4. User speaks → `user_transcript` events; assistant replies → `assistant_delta` /
   `assistant_done`; barge-in → `interrupted` (rendering must cut the same frame).
5. Hotkey again or Esc → `POST /voice/interrupt` (mid-speech) or `POST /voice/stop` (end session).
   Session end emits `session_ended`; overlay shows a brief summary and dismisses.

### Wake word (Phase 6, opt-in, default off)
1. Daemon runs an on-device openWakeWord loop (no network). On detection it emits
   `wake_detected` on `/voice/events` and auto-starts the realtime session.
2. App (already subscribed) shows the overlay and joins the running session via the snapshot.
3. The OpenAI realtime socket opens only **after** local detection — idle audio never leaves the
   machine. Mic-active state is always visible in the menu bar. Auto-pause on screen lock.

### Approval
1. Agent run blocks on an approval (existing orchestrator behavior).
2. App polls `GET /approvals` (10s while healthy); new pending item → native notification with
   Approve/Deny actions → `POST /approvals/{id}/approve|deny` (existing endpoints, audit-logged).
3. Approvals view in the main window is the durable console; notifications are transient.

### Activity browsing
Main window reads `GET /activity?days&limit&tz` (day-grouped items + stats) and
`GET /activity/{id}` for transcript/run detail. All aggregation is server-side.

### Settings change
`GET /settings` renders panes (env-overridden keys are read-only with a "managed by environment"
badge); `PUT /settings` validates against the whitelist, writes atomically, and returns the
effective merged config.

## 6. Data model and ownership

| Data | Owner | Store |
|---|---|---|
| Sessions, tasks, runs, approvals, memory, audit | Python daemon | SQLite state DB (existing) |
| Mutable settings (wake words, voice, models, wake-word toggle) | Python daemon | `settings.json` in the Iris state dir, beneath env overrides |
| Secrets (`OPENAI_API_KEY`, notifier tokens) | Python daemon | environment / `.env` (never via API) |
| Gateway token | Swift app | Keychain (device-only) |
| App prefs (port, hotkey, onboarding flag) | Swift app | `UserDefaults` (no secrets) |
| Logs | each process | daemon: `~/Library/Logs/Iris/daemon.log` (JSON, rotated); app: `os_log` subsystem `com.bethvour.iris` |

The Swift app holds **no domain state** — everything it renders is fetched, so a daemon restart
or an app relaunch cannot desynchronize them beyond one refresh.

## 7. External integrations

- **OpenAI Realtime (wss)** — live speech-to-speech; opened per session by the daemon.
- **OpenAI API (https)** — agent runs, transcription, memory extraction (existing).
- **Google Vision** (optional) — OCR for screen reading (existing).
- **Pushover/ntfy** (optional) — phone notifications (existing; native notifications make these
  redundant on the Mac itself).

All outbound; the daemon listens on loopback only.

## 8. Failure modes

| # | Failure | Detection | User experience | Recovery |
|---|---|---|---|---|
| F1 | Daemon process dies | DaemonManager termination handler; health poll | Menu bar error badge; overlay shows "Iris isn't running" + Restart | Supervised restart w/ backoff |
| F2 | Daemon crash-loops (≥3/60s) | DaemonManager counter | "Something's wrong" + Open Diagnostics (stderr tail, log viewer) | Manual: diagnostics, settings reset, reinstall; documented in runbook |
| F3 | Port 8765 occupied by non-Iris process | Health probe returns non-Iris/invalid response | Explicit port-conflict state with the owning process hint | Free the port or change port in Settings → Advanced |
| F4 | Token mismatch (Keychain rotated/deleted, stale daemon) | 401 from any authed call against healthy daemon | "Reconnect needed" state | Token rotation flow: regenerate, restart daemon with new env |
| F5 | OpenAI unreachable / key invalid | Voice session emits terminal `error` event with code | Overlay error state: "Couldn't reach OpenAI" / "API key problem" + retry or open Settings → Account | Retry; fix key in onboarding/settings |
| F6 | Mic permission denied/revoked | `AVCaptureDevice.authorizationStatus` + session error | Overlay/onboarding state with System Settings deep link | User grants in System Settings; app detects on next activation |
| F7 | SSE stream stalls | 2 missed heartbeats (15s cadence) | "Reconnecting…" chip; UI marked stale, never silently frozen | SSEClient reconnect (jittered backoff); snapshot-on-connect restores state |
| F8 | Double start (session already live) | `POST /voice/start` → 409 | None — app adopts the running session from the snapshot | Automatic |
| F9 | `settings.json` corrupt | Parse failure on load | Daemon starts on defaults; warning logged; Settings shows defaults | Atomic writes prevent most cases; reset procedure in runbook |
| F10 | Mac sleeps mid-session | Realtime socket drops; session emits `error`/`session_ended` | Overlay ends session cleanly on wake | New session on next activation |

Every failure path must end in a state with exactly one primary recovery action. No raw error
codes in user-facing surfaces; full detail goes to logs and Diagnostics.

## 9. Observability

- **Daemon**: structured JSON logs (`ts, level, event, component, session_id/run_id, detail`)
  with rotation; request logs carry path/status/duration, never tokens or bodies; voice events
  mirrored at DEBUG.
- **App**: `os.Logger`, subsystem `com.bethvour.iris`, categories per component (`daemon`,
  `api`, `sse`, `overlay`, `hotkey`). State transitions logged with the same session ids the
  daemon uses, so one grep correlates both processes.
- **Health**: `GET /health` (unauthenticated) carries version/pid/uptime; DaemonManager polls it;
  `GET /voice/status` exposes controller state + SSE subscriber count for diagnostics.
- **Support bundle**: Settings → Advanced → Export Diagnostics zips daemon log tail, app log
  extract, redacted effective config, and versions. Verified secret-free by test.
- **No remote telemetry.** Iris is local-first; nothing is phoned home.

## 10. Deployment shape

- **Dev**: `uv run iris serve` from the repo (run by the app in dev mode, or adopted from a
  terminal); Swift app run from Xcode.
- **Release**: `Iris.app` containing the Swift binary plus an embedded relocatable Python runtime
  (`Contents/Resources/iris-runtime/`, built from python-build-standalone + the uv lockfile).
  Signed inside-out with Developer ID + hardened runtime, notarized, shipped as a DMG.
- **Rollback**: previous tagged release artifacts stay published; rollback is reinstalling the
  prior DMG. Daemon state schema changes must remain backward-compatible one release back.
