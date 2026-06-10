# Iris Gateway — Desktop API Contract

Status: **frozen contract** for the desktop implementation. Swift `Codable` models
(`apps/macos/IrisKit/Models.swift`) and Python handlers are both written against this document.
Changes are additive-only once Phase 2 starts; anything breaking requires bumping
`contract_version` and updating both sides in one change.

Existing endpoints that the desktop app reuses (approvals, messages, runs) are documented in
§8 with their **current** shapes — they are not being redesigned.

## 1. Conventions

- **Base URL**: `http://127.0.0.1:8765` (port configurable; loopback only).
- **Auth**: `Authorization: Bearer <gateway-token>` on every request. Exemptions are unchanged:
  `/health`, `/dashboard`, `/memory-browser`. All endpoints introduced by this contract
  (`/voice/*`, `/activity*`, `/settings`) **require auth**, including SSE.
- **Content type**: `application/json; charset=utf-8` (SSE: `text/event-stream`).
- **Timestamps**: ISO 8601 UTC with `Z` suffix (`2026-06-10T14:03:21Z`). Day grouping uses the
  client-supplied IANA `tz` query parameter.
- **Request limits**: bodies ≤ 1 MB; list endpoints cap `limit` server-side.
- **Versioning**: `GET /health` carries `contract_version` (starts at `1`). Clients refuse to
  operate against a higher major version than they know.

### Error envelope (new endpoints)

```json
{ "error": { "code": "voice_already_running", "message": "A voice session is already active." } }
```

`code` is a stable machine-readable snake_case string; `message` is human-readable and safe to
display. Legacy endpoints (§8) return the historical flat shape `{"error": "<string>"}`; the
Swift client normalizes both into `IrisAPIError`.

Common codes: `unauthorized` (401), `invalid_request` (400), `not_found` (404),
`conflict` (409), `internal` (500).

## 2. Health (extended)

`GET /health` — unauthenticated.

```json
{
  "ok": true,
  "agent": "Iris",
  "version": "0.4.0",
  "contract_version": 1,
  "pid": 4242,
  "started_at": "2026-06-10T13:58:02Z"
}
```

`version` is the iris package version. DaemonManager treats a response that fails to parse as
"port occupied by a non-Iris process" (failure F3 in the architecture doc).

## 3. Voice control

A daemon hosts **at most one** live voice session. All voice routes require auth.

### `POST /voice/start`

Request:

```json
{ "mode": "conversation" }
```

`mode` ∈ `"conversation"` (wake-gating off — hotkey/wake-word already established intent).
Future modes are additive.

Responses:
- `202` — session starting:

```json
{ "session": { "id": "voice-9f2c…", "mode": "conversation", "started_at": "…", "state": "connecting" } }
```

- `409 voice_already_running` — body includes the live session descriptor under `session` so the
  client can adopt it instead of erroring.
- `400 invalid_request` — unknown mode.
- `500 voice_unavailable` — e.g. `OPENAI_API_KEY` missing; message says which precondition failed.

### `POST /voice/stop`

Ends the session gracefully (summarize-to-memory runs as in CLI mode). **Idempotent**: stopping
when no session is live returns `200 {"ok": true, "was_running": false}`.

### `POST /voice/interrupt`

Barge-in: cancels the in-flight assistant response but keeps the session listening. `200`
`{"ok": true}`; `409 voice_not_running` if there is no session; `200 {"ok": false}` if there was
nothing to interrupt (no active response).

### `GET /voice/status`

```json
{
  "state": "speaking",
  "session": { "id": "voice-9f2c…", "mode": "conversation", "started_at": "…" },
  "subscribers": 1,
  "meeting_active": false
}
```

`session` is `null` when `state` is `idle`. `subscribers` = live SSE connections (diagnostics).

### Session state machine

```
idle ─► connecting ─► listening ─► user_speaking ─► transcribing ─► thinking ─► speaking ─┐
  ▲         │             ▲   ▲                                        │           │       │
  │         ▼ (error)     │   └────────────── interrupted ◄────────────┴───────────┘       │
  └── session_ended ◄─────┴───────────────────────────────────────────────────────────────┘
                       (meeting: listening ─► meeting ─► listening)
```

| State | Meaning (maps to `voice.py` observables) |
|---|---|
| `idle` | No session. |
| `connecting` | Realtime websocket connecting / `session.update` sent, awaiting `session.updated`. |
| `listening` | Session ready, mic open, nothing in flight. |
| `user_speaking` | `input_audio_buffer.speech_started` (not a barge-in). |
| `transcribing` | `speech_stopped`, awaiting transcript completion. |
| `thinking` | Turn dispatched (model response begun or agent run in flight). |
| `speaking` | `response.created` through response done; assistant audio playing. |
| `meeting` | Silent meeting mode (listening, not responding). |
| `error` | Terminal failure; carries the error payload. Next valid state: `idle`. |

`interrupted` is an **event**, not a state — after barge-in the session lands in `user_speaking`
(the interrupting speech) and proceeds normally.

## 4. Voice event stream — `GET /voice/events` (SSE)

Auth required (header-based; native clients can set headers on SSE).

- On connect the server **always** sends a `state` snapshot first, whether or not a session is
  live. Clients render entirely from snapshot + deltas; reconnect mid-session is lossless for
  state (transcript history inside the live turn may be truncated to the latest snapshot).
- Heartbeat: an SSE comment (`: hb`) every 15s. Two missed heartbeats ⇒ client treats the
  stream as stale and reconnects.
- Frame format: `event: <type>` + `data: <json>`.

### Event catalog

| `event:` | `data` payload | Notes |
|---|---|---|
| `state` | `{"state": "...", "session": {...}\|null, "meeting_active": bool}` | Snapshot; first frame on connect and on every state transition. |
| `wake_detected` | `{"source": "wake_word"\|"in_session", "phrase": "hey iris"}` | Phase 6 local detector, or in-session wake while gated. |
| `user_transcript` | `{"text": "…", "final": true}` | Completed user turn transcript (post wake-word stripping). |
| `assistant_delta` | `{"text": "…"}` | Streamed assistant transcript delta. |
| `assistant_done` | `{"text": "…"}` | Full deduplicated assistant turn text. |
| `interrupted` | `{"at_ms": 1234}` | Barge-in: assistant response cut; `at_ms` = played audio offset. Render must halt same-frame. |
| `tool_call` | `{"name": "…", "status": "started"\|"done"\|"failed"}` | Realtime tool dispatch visibility. |
| `agent_run` | `{"run_id": "…", "status": "started"\|"done"\|"failed", "summary": "…"}` | Long task handed to the orchestrator; links to `/runs/{id}`. |
| `meeting` | `{"active": bool, "meeting_id": "…"}` | Meeting mode toggled. |
| `session_ended` | `{"reason": "stopped"\|"error"\|"daemon_shutdown", "duration_seconds": 312}` | Terminal for the session. |
| `error` | `{"code": "…", "message": "…", "terminal": bool}` | `terminal: true` ⇒ session is over; `false` ⇒ recoverable (e.g. transient audio warning). |

Unknown event types MUST be ignored by clients (additive evolution).

## 5. Activity feed

### `GET /activity?days=7&limit=50&cursor=<opaque>&tz=America/New_York`

Bounds: `days ≤ 90`, `limit ≤ 200`. `tz` defaults to UTC.

```json
{
  "stats": {
    "sessions_this_week": 12,
    "runs_completed": 34,
    "runs_failed": 2,
    "last_active_at": "2026-06-10T13:01:11Z"
  },
  "days": [
    {
      "date": "2026-06-10",
      "label": "today",
      "items": [
        {
          "id": "act-…",
          "kind": "voice_session" ,
          "time": "2026-06-10T13:01:11Z",
          "title": "Voice session",
          "preview": "Make sure there is no double rounded highlight…",
          "status": "done"
        }
      ]
    }
  ],
  "next_cursor": "…|null"
}
```

`kind` ∈ `voice_session | run | meeting | message`. `status` ∈ `done | failed | cancelled |
blocked | running`. `label` ∈ `today | yesterday | null` (older days render the date).

### `GET /activity/{id}`

```json
{
  "id": "act-…",
  "kind": "voice_session",
  "time": "…",
  "status": "done",
  "title": "Voice session",
  "transcript": [
    { "role": "user", "text": "…", "time": "…" },
    { "role": "assistant", "text": "…", "time": "…" }
  ],
  "run": { "run_id": "…", "events_url": "/runs/…/events" } 
}
```

`transcript` and `run` are each optional by `kind`. `404 not_found` for unknown ids.

## 6. Settings

### `GET /settings`

```json
{
  "settings": {
    "wake_words":     { "value": ["iris", "hey iris"], "source": "settings", "mutable": true },
    "voice":          { "value": "marin",              "source": "default",  "mutable": true },
    "realtime_model": { "value": "gpt-realtime",       "source": "env",      "mutable": false },
    "wake_word_enabled": { "value": false,             "source": "default",  "mutable": true },
    "notify_provider":   { "value": null,              "source": "default",  "mutable": true }
  },
  "secrets": {
    "openai_api_key": { "is_set": true },
    "pushover_token": { "is_set": false }
  }
}
```

- `source` ∈ `env | settings | default` (precedence: env > settings file > default).
- `mutable: false` exactly when `source == "env"` — the UI renders these disabled with a
  "managed by environment" badge.
- **Secret values never appear in any response.** Only `is_set`.

### `PUT /settings`

Request — partial update, whitelisted keys only:

```json
{ "wake_words": ["iris", "hey iris"], "voice": "cedar" }
```

- `200` — returns the same shape as `GET /settings` (the effective merged result).
- `400 invalid_request` — unknown key, wrong type, or invalid value; `message` names the key.
- `409 conflict` `setting_env_managed` — attempted write to an env-overridden key.

Writes are atomic (temp file + rename). Changes are audit-logged by key name (values omitted for
sensitive keys). A live voice session picks up changes on its next start, not mid-session.

## 7. Status codes summary (new endpoints)

| Code | Meaning |
|---|---|
| 200 / 202 | Success / accepted (session starting) |
| 400 | Validation failure — `invalid_request` |
| 401 | Missing/invalid bearer token — `unauthorized` |
| 404 | Unknown resource — `not_found` |
| 409 | State conflict — `voice_already_running`, `voice_not_running`, `setting_env_managed` |
| 500 | Internal — `internal`, `voice_unavailable` |

## 8. Existing endpoints used by the desktop app (current shapes, unchanged)

| Endpoint | Shape notes |
|---|---|
| `GET /approvals` | `{"approvals": [...]}` |
| `POST /approvals/{id}/approve` / `…/deny` | `{"ok": bool, "status": "approved"\|"denied"}`; decisions audit-logged |
| `POST /messages` | `{"message": "…"}` → `{ok, session_id, task_id, run_id, message, payload, task_status}` |
| `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events` | run listings/detail; SSE variant streams run events until terminal status |
| `POST /runs/{id}/cancel` | `{"ok": bool}` |
| `GET /sessions`, `GET /sessions/{id}` | session listings/detail |

These return flat `{"error": "<string>"}` on failure; `IrisAPIError` normalizes.

## 9. Client obligations (Swift)

1. Send the bearer token on every request including SSE; treat 401 as the token-mismatch flow
   (F4), never as a retry.
2. Render voice UI only from the SSE stream (snapshot + deltas); never poll `/voice/status` for
   rendering (it exists for diagnostics).
3. Ignore unknown SSE event types and unknown JSON fields (`Codable` with lenient decoding).
4. Treat 2 missed heartbeats as staleness: show "reconnecting", reconnect with jittered backoff.
5. On `409 voice_already_running`, adopt the running session (subscribe and render snapshot).
6. Never log the token, request bodies, or secret values.
