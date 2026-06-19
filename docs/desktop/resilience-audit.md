# Resilience Audit (Step 7.3)

A scripted fault-injection drill that converts the failure-mode table in
[`architecture.md` §8](architecture.md) (F1–F10) into _verified_ behavior. Every
scenario must end in a user-comprehensible state with exactly one primary
recovery action — never a frozen surface, a blank window, or a raw stack trace.

This document is the audit record: **scenario → expected → observed → fix**.
Re-run it before every release and after any change to the daemon supervisor,
the settings/secrets layer, or the voice session lifecycle.

- **Status legend:** ✅ verified · ⚠️ verified with caveat · 🔲 manual, pending run
- **Last automated run:** 2026-06-19 (`apps/macos/scripts/resilience_drill.py`, 6/6 passed)

---

## 1. Automated daemon-side drills

These run deterministically without a GUI, microphone, or network access to
OpenAI. They drive real `iris serve` subprocesses through fault injection and
assert on HTTP responses, exit codes, and structured log events.

```sh
# from the repo root
apps/macos/scripts/resilience_drill.sh
# or
uv run python apps/macos/scripts/resilience_drill.py
```

Exit code is `0` only when every drill passes. Each drill runs in its own temp
`IRIS_STATE_DB` + log dir and strips the developer's `IRIS_*` / `OPENAI_API_KEY`
shell overrides so results are reproducible on any machine.

| FM | Scenario | Expected | Observed (2026-06-19) | Status |
|----|----------|----------|------------------------|--------|
| F3 | Configured port already in use | Daemon fails fast with a clear port-conflict message, **exit code 2**, `gateway.port_in_use` logged, no traceback | `exit_code=2; clean_message=True; no_traceback=True; port_in_use_logged=True` | ✅ (fix #1) |
| F9 | `settings.json` corrupt | Daemon starts on the env/default layer; `settings.corrupt_file_ignored` logged; `GET /settings` returns a well-formed payload; no crash | `health=ok; GET /settings=200; well_formed_payload=True; corrupt_file_ignored_logged=True` | ✅ |
| F4 | Token mismatch (stale daemon / rotated Keychain item) | Authed call with the wrong token → `401`; correct token → `200`; `/health` stays unauthenticated | `wrong_token=401; right_token=200; health=200` | ✅ |
| F1 | Daemon process dies (SIGKILL) + clean restart | SIGKILL of the daemon process group frees the port; a fresh daemon binds the same port and becomes healthy | `port_freed_after_kill=True; restart_healthy=True` | ✅ |
| F5 | OpenAI key absent → terminal voice error | `POST /voice/start` accepted (`202`), the session settles into a non-live terminal state, daemon stays healthy (no crash) | `voice/start=202; settled_state='idle'; health_after=200` | ✅ |
| F8 | Double start (session already live) | Two `POST /voice/start` calls never crash the daemon; a live session yields `409` with the running-session payload so the app can adopt | `start1=202; start2=409; health_after=200; start2_has_session=True` | ✅ |

> **F1 note for the supervisor.** The drill must SIGKILL the daemon's *process
> group*, not the `uv run` launcher — killing only the wrapper orphans the real
> python daemon, which keeps holding the port. `DaemonManager` must likewise
> track and terminate the actual daemon PID (and, in dev mode where it spawns
> `uv run iris serve`, the whole group), or supervised restart will hit a
> spurious port conflict. Verify against `DaemonManager.swift` during 7.5.

---

## 2. Manual drills (release build required)

These require a signed release build, real TCC permissions, a microphone, and a
person at the machine. Run each on a clean test account; capture a screenshot of
every user-facing state into `docs/desktop/screenshots/resilience/` and fill in
the **Observed** column. Map the app surface to the real `DaemonState` /
`ProbeOutcome` cases in `apps/macos/IrisKit/DaemonState.swift`.

### 2.1 — F1/F2 SIGKILL the daemon mid-session

1. Start a voice session from the overlay.
2. `kill -9 $(pgrep -f "iris serve")` while it is listening/speaking.
3. **Expected:** overlay drops to an error state ("Iris isn't running" + Restart);
   `DaemonManager` transitions `healthy → unhealthy → restarting` and recovers
   with backoff; menu bar reflects the transition. Forcing ≥3 crashes/60s must
   land in `crashLooping(stderrTail:)` — a terminal state with **Open
   Diagnostics**, not an infinite retry.
- **Observed:** 🔲
- **Recovery action surfaced:** 🔲

### 2.2 — F3 Occupy port 8765 before launch

1. `nc -l 127.0.0.1 8765 &` (or run a second daemon), then launch the app.
2. **Expected:** `ProbeOutcome.conflict` → `DaemonState.portConflict(reason:)`
   with the owning-process hint; explicit port-conflict UI; no silent port
   hopping. Changing the port in Settings → Advanced recovers.
- **Observed:** 🔲

### 2.3 — F9 Corrupt `settings.json` (UI side)

1. Write garbage into `settings.json` next to the state DB; launch.
2. **Expected:** daemon starts on defaults (verified automatically in §1);
   Settings window shows default values with a non-fatal warning; saving
   rewrites a valid file (atomic).
- **Observed:** 🔲

### 2.4 — F6 Revoke each TCC permission mid-use

Run once per permission: **Microphone**, **Accessibility**, **Screen
Recording**. Revoke in System Settings while Iris is running, then trigger a
feature that needs it.

1. **Expected:** the feature surfaces a permission state with a "Open System
   Settings" deep link (not a crash or silent no-op); after re-granting, the app
   detects it on the next activation. `AVCaptureDevice.authorizationStatus` (mic)
   drives the overlay/onboarding state.
- **Observed (mic):** 🔲   **(accessibility):** 🔲   **(screen):** 🔲

### 2.5 — F5 OpenAI unreachable / invalid key (UI side)

1. Block network egress (or set an invalid `OPENAI_API_KEY`), start a session.
2. **Expected:** overlay error state — "Couldn't reach OpenAI" / "API key
   problem" — with retry or **Open Settings → Account**. Terminal `error` voice
   event with a code (daemon path verified in §1 F5). Timeout, not a hang.
- **Observed (unreachable):** 🔲   **(invalid key):** 🔲

### 2.6 — F4 Keychain item deleted (re-bootstrap)

1. Delete the Iris gateway-token Keychain item; relaunch.
2. **Expected:** app detects the missing/mismatched token, regenerates it, and
   restarts the daemon with the new env (token-rotation flow). Authed calls
   recover; `DaemonState.tokenMismatch` is transient, not terminal.
- **Observed:** 🔲

### 2.7 — F10 Mac sleep/wake mid-session

1. Start a session; `pmset sleepnow`; wake after ~30s.
2. **Expected:** the realtime socket drop emits `error`/`session_ended`; the
   overlay ends the session cleanly on wake (no zombie listening state); a new
   activation starts a fresh session.
- **Observed:** 🔲

### 2.8 — Activity history at scale (10k items)

1. Seed ~10,000 activity rows; open the activity feed; scroll top→bottom.
2. **Expected:** smooth scroll (no dropped frames / spinner stalls); pagination
   holds; daemon RSS stays flat across the scroll (no per-row leak).
- **Observed (scroll):** 🔲   **(daemon RSS before/after):** 🔲

---

## 3. Findings & fixes

### Fix #1 — F3: raw `OSError` on port conflict → typed `PortInUseError`

- **Found:** `GatewayService.serve()` let `ThreadingHTTPServer((host, port), …)`
  raise a bare `OSError: [Errno 48] Address already in use`, which propagated
  through `cmd_serve` as an uncaught traceback (exit 1). Violates §8 F3 ("explicit
  port-conflict state", "no silent port hopping") and pollutes `daemon.log`.
- **Fix:** `serve()` catches `EADDRINUSE`/`EADDRNOTAVAIL`, logs structured
  `gateway.port_in_use` (host/port/detail), and raises a typed `PortInUseError`.
  `cmd_serve` catches it, prints a one-line operator message to stderr, and
  returns **exit code 2** so a supervisor can distinguish a port conflict from an
  unexpected crash.
- **Files:** `src/iris/gateway.py`, `src/iris/cli.py`.
- **Regression test:** `tests/test_gateway_health.py::test_serve_on_occupied_port_raises_clean_port_in_use`.

_No other findings in the automated drills; F1/F4/F5/F8/F9 already behaved to
contract and now have standing regression coverage via the drill harness._

---

## 4. Coverage vs. architecture §8

| FM | Failure | Coverage |
|----|---------|----------|
| F1 | Daemon process dies | §1 (daemon restart) ✅ + §2.1 (UI/backoff) 🔲 |
| F2 | Daemon crash-loops | §2.1 (crashLooping terminal state) 🔲 |
| F3 | Port occupied | §1 ✅ (fix #1) + §2.2 (UI) 🔲 |
| F4 | Token mismatch | §1 ✅ + §2.6 (re-bootstrap) 🔲 |
| F5 | OpenAI unreachable | §1 ✅ + §2.5 (UI) 🔲 |
| F6 | Mic/TCC denied | §2.4 🔲 (TCC is GUI-only) |
| F7 | SSE stream stalls | heartbeat/reconnect — covered by SSE tests; add a manual stall drill in 7.5 |
| F8 | Double start | §1 ✅ |
| F9 | `settings.json` corrupt | §1 ✅ + §2.3 (UI) 🔲 |
| F10 | Mac sleeps mid-session | §2.7 🔲 |
