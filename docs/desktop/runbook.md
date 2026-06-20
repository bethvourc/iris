# Iris for macOS — Operational Runbook

The page you want at 11pm when the mic is broken. Every command here is meant to
be run verbatim. Audience: you, future-you, and anyone supporting an Iris
install. For the failure-mode catalog and verified behaviors see
[`resilience-audit.md`](resilience-audit.md); for architecture see
[`architecture.md`](architecture.md).

Bundle identifier: **`com.bethvour.iris`**. Default gateway: **`127.0.0.1:8765`**.

---

## 1. Where everything lives

| What | Location |
|------|----------|
| App | `/Applications/Iris.app` |
| Daemon log (JSON lines) | `~/Library/Logs/Iris/daemon.log` (+ rotated `daemon.log.1…`) |
| App logs | unified log, subsystem `com.bethvour.iris` (see §3) |
| **State DB + `settings.json` (release)** | `~/Library/Application Support/Iris/` (`iris.sqlite3`, `settings.json`) |
| State DB + `settings.json` (dev checkout) | `<repo>/build/iris.sqlite3`, `<repo>/build/settings.json` |
| Gateway token | Keychain — service `com.bethvour.iris`, account `gateway-token` |
| App preferences | `UserDefaults` domain `com.bethvour.iris` (`gatewayPort`, `onboardingComplete`) |
| Launch-at-login | `SMAppService.mainApp` (System Settings → General → Login Items) |
| TCC permissions | Microphone, Accessibility, Screen Recording (granted to `Iris.app`) |

> **Dev vs. release.** If a repo checkout is detected the app runs the daemon
> via `uv run iris serve` with state under `<repo>/build/`. A shipped app runs
> the embedded runtime (`Iris.app/Contents/Resources/iris-runtime/…`) with state
> pinned under Application Support. The daemon never holds secrets on disk: the
> token is injected via the `IRIS_GATEWAY_TOKEN` environment variable only.

---

## 2. Is it alive? (health check)

`/health` is unauthenticated and is the supervisor's contract:

```sh
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```

A healthy daemon returns:

```json
{ "ok": true, "agent": "Iris", "version": "0.1.0",
  "contract_version": 1, "pid": 12345, "started_at": "2026-06-19T13:20:00Z" }
```

- **Connection refused** → daemon not running (F1). Use the menu bar **Restart
  Iris**, or relaunch the app.
- **Replies but not JSON / different shape** → something else owns the port (F3).
  See §5.
- **Healthy but the app shows "token rejected"** → token mismatch (F4). See the
  token-rotation reset in §4.

Authenticated probe (proves the token is accepted):

```sh
TOKEN=$(security find-generic-password -s com.bethvour.iris -a gateway-token -w)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/voice/status
```

---

## 3. Reading the logs

**Daemon** — structured JSON, one object per line:

```sh
tail -f ~/Library/Logs/Iris/daemon.log
# pretty-print + filter to a component:
tail -n 500 ~/Library/Logs/Iris/daemon.log | python3 -c \
  'import sys,json;[print(json.loads(l).get("event"),json.loads(l).get("detail","")) for l in sys.stdin if l.strip()]'
```

Each line carries `ts, level, event, component, detail` (and `session_id` /
`run_id` where relevant). Tokens and request bodies are never logged.

**App** — unified log, subsystem `com.bethvour.iris`, one category per component
(`app`, `daemon`, `api`, `sse`, `hotkey`, `approvals`, `diagnostics`):

```sh
log show  --predicate 'subsystem == "com.bethvour.iris"' --last 1h --info
log stream --predicate 'subsystem == "com.bethvour.iris"'            # live
```

State transitions are logged with the same session ids the daemon uses, so one
grep correlates both processes.

---

## 4. Reset procedures

Order matters: quit Iris first for anything that touches state or the token.

### 4a. Rotate the gateway token (recovers F4 token mismatch)

In-app: **Settings → Account → Rotate token** (generates a new Keychain token and
restarts the daemon with it). Manual equivalent:

```sh
# Quit Iris, then:
security delete-generic-password -s com.bethvour.iris -a gateway-token
# Relaunch Iris — it regenerates the token (loadOrCreate) and restarts the daemon.
```

### 4b. Reset settings (keeps history/memory)

```sh
# Quit Iris, then (release path):
rm -f ~/Library/"Application Support"/Iris/settings.json
# Relaunch — the daemon starts on defaults (corrupt/missing file degrades, never crashes).
```

### 4c. Full state reset (wipes history, memory, settings, token)

```sh
# 1. Quit Iris.
# 2. Remove state, settings, and logs:
rm -rf ~/Library/"Application Support"/Iris
rm -rf ~/Library/Logs/Iris
# 3. Forget the token:
security delete-generic-password -s com.bethvour.iris -a gateway-token 2>/dev/null
# 4. Reset app preferences (port, onboarding flag):
defaults delete com.bethvour.iris 2>/dev/null
# 5. Relaunch — onboarding runs again from a clean slate.
```

---

## 5. Free a busy port (F3)

The daemon refuses to silently port-hop: a busy `8765` logs `gateway.port_in_use`
and exits cleanly (code 2). Find and free the owner, or change the port.

```sh
lsof -nP -iTCP:8765 -sTCP:LISTEN          # who owns it?
# If it's a stale Iris daemon, kill it:
pkill -f "iris serve"
# Or change the port: Settings → Advanced → Port, then restart Iris.
```

---

## 6. Uninstall (clean removal)

```sh
# 1. Quit Iris (menu bar → Quit Iris).
# 2. Remove launch-at-login: System Settings → General → Login Items → remove Iris
#    (or toggle Settings → General → Launch at login off before quitting).
# 3. Delete the app:
rm -rf /Applications/Iris.app
# 4. Remove data, logs, token, prefs:
rm -rf ~/Library/"Application Support"/Iris ~/Library/Logs/Iris
security delete-generic-password -s com.bethvour.iris -a gateway-token 2>/dev/null
defaults delete com.bethvour.iris 2>/dev/null
# 5. Revoke TCC grants so a reinstall re-prompts cleanly:
tccutil reset Microphone    com.bethvour.iris
tccutil reset Accessibility com.bethvour.iris
tccutil reset ScreenCapture com.bethvour.iris
```

---

## 7. Failure signatures → remedies

| Symptom / log signature | Meaning | Remedy |
|---|---|---|
| Menu "Iris is stopped"; `curl /health` refused | Daemon not running (F1) | Menu → **Start/Restart Iris** |
| Menu "Iris keeps crashing"; repeated `gateway.stop` + nonzero exits | Crash-loop (F2, terminal) | **Export Diagnostics** (§8), inspect `daemon.log`; if config-induced, §4b then §4c |
| `gateway.port_in_use` in `daemon.log`; menu "Port conflict: …" | Port 8765 taken (F3) | §5 |
| Authed calls 401; menu "Connection token rejected" | Token mismatch (F4) | §4a (rotate) |
| Overlay "Couldn't reach OpenAI" / "API key problem" | OpenAI unreachable / bad key (F5) | Check network; fix key in Settings → Account |
| Overlay "Microphone access is off" | Mic TCC denied (F6) | System Settings → Privacy → Microphone → enable Iris |
| `settings.corrupt_file_ignored` in `daemon.log` | `settings.json` was invalid | Already degraded to defaults; §4b to rewrite a clean file |
| Overlay "Reconnecting…" that never clears | SSE stalled (F7) | Usually self-heals; if not, Restart Iris |

The invariant: every user-facing failure shows exactly one recovery action. Raw
detail lives only in the logs and the diagnostics bundle.

---

## 8. Diagnostics bundle

**Settings → Advanced → Export Diagnostics…** builds a redacted zip and presents
a save panel. Contents:

- `summary.txt` — Iris/app version, macOS version, generation timestamp.
- `settings.json` — effective settings (secrets reported as presence only).
- `daemon.log` — recent daemon log tail.

Every file is run through a secret redactor (OpenAI `sk-…`, Groq `gsk_…`, Resend
`re_…`, and `Bearer …` / `token=…` catch-alls → `***REDACTED***`) before it is
written, so the zip is safe to share. To read it: start with `summary.txt` for
versions, then scan `daemon.log` for the signatures in §7; `settings.json` shows
whether a key is set and where its value comes from (`env` / `settings` /
`default`).
