# Iris

Iris is a local Mac agent prototype. It runs from the terminal, can capture the
live screen, can use voice/text interaction, and can control macOS through a
safety gate.

## Setup

```bash
cd /Users/clintonimaro/Documents/Projects/iris
uv sync --dev --no-editable
cp .env.example .env
```

Fill `.env` with `OPENAI_API_KEY` and, if you want OCR/label detection,
`GOOGLE_APPLICATION_CREDENTIALS`. Iris defaults to `gpt-5.5` for chat/screen
reasoning, `gpt-realtime-2` for voice, and `computer-use-preview` for UI
navigation. Override the model IDs in `.env` when your account needs different
model IDs.

macOS will also need privacy permissions:

- Microphone: voice input.
- Screen Recording: screenshots.
- Accessibility: click/type/hotkey automation.
- Automation: AppleScript interactions with Finder/System Events.

## Commands

```bash
./iris status
./iris init
./iris providers
./iris profile show
./iris profile set --name Clinton --pronouns he/him
./iris permissions
./iris test-notify
./iris notify test
./iris test-screen
./iris test-voice
./iris test-voice --listen
./iris test-vision
./iris test-control
./iris watch add --kind web --name "Example" --target https://example.com --expected Example
./iris watch list
./iris memory add --category project --content "Iris is local-first."
./iris meeting start --silent --title "Planning"
./iris meeting stop
./iris workflows
./iris run repo-debug-pr
./iris approvals
./iris audit
./iris serve
./iris sessions list
./iris tasks list
./iris plugins list
./iris start
./iris start --wake
./iris start --live
```

`iris start` opens an interactive conversational session. Type `/live` or start
with `./iris start --live` / `./iris start --wake` to use live speech-to-speech mode. Iris keeps a
Realtime session open, streams microphone audio, uses server VAD, waits for
`iris` or `hey iris`, and plays streamed audio responses as they arrive. Tune it
with `IRIS_WAKE_WORDS`, `IRIS_WAKE_POLL_SECONDS`, and `IRIS_LISTEN_SECONDS`.

`/listen` remains as a backup speech-to-text mode when live speech is not
available.

Iris infers your first name from macOS, or you can save it explicitly with
`./iris profile set --name Clinton --pronouns he/him`. Saved profile values are
stored in local memory and included in the chat prompt so Iris can address you
personally without repeating your name every sentence.

Type `/listen` or `/listen 1.5` to record one voice request through OpenAI
Realtime, or type normally. Iris keeps short-lived conversation state in memory
for the active process, so you can chat, follow up, ask what it sees, and ask it
to act. Type `/reset` to clear the current chat state. The kill hotkey pauses
automation immediately. Global hotkeys are best-effort until macOS grants
Accessibility.

## Phone Notifications

Iris can send terminal-first phone push notifications through Pushover by
default. Install Pushover on your phone, create an app token, then set:

```bash
IRIS_NOTIFY_PROVIDER=pushover
PUSHOVER_TOKEN=...
PUSHOVER_USER=...
```

ntfy remains supported. Set `IRIS_NOTIFY_PROVIDER=ntfy`, subscribe to an
unguessable topic in the ntfy phone app, then set `IRIS_NTFY_TOPIC`.

```bash
./iris test-notify --message "Iris is online."
```

This is the foundation for commands like "watch this page/app and notify me when
X changes." The first production version should send notifications only for
user-approved watch jobs and summaries, not for every background observation.

## Agent Gateway And Durable Tasks

`./iris serve` starts a local HTTP gateway on `127.0.0.1:8765`. The gateway
creates persistent sessions, records user/assistant messages, wraps each
message in a durable task, and exposes approval/task endpoints for future phone
and app surfaces.

Useful commands:

```bash
./iris serve
./iris sessions list
./iris sessions show <session_id>
./iris tasks list
./iris tasks show <task_id>
./iris tasks cancel <task_id>
./iris tasks resume <task_id>
./iris plugins list
./iris plugins health
```

Local API endpoints:

```text
POST /messages
GET /sessions
GET /sessions/:session_id
GET /tasks
GET /tasks/:task_id
POST /tasks/:task_id/cancel
POST /tasks/:task_id/resume
GET /approvals
POST /approvals/:approval_id/approve
POST /approvals/:approval_id/deny
```

## Local Agent OS Foundation

`iris init` creates the local SQLite state DB. Iris stores watch rules, watch
runs, meeting records, memory, sessions, durable tasks, approvals, workflow
runs, rollback records, and audit events there. The current terminal version
implements:

- provider/model registry for OpenAI, Groq, Pushover, and ntfy
- Pushover and ntfy notification adapters
- local agent gateway with sessions, messages, task records, and approval APIs
- plugin registry for built-in browser, Mac app, media, Gmail, file, and workflow adapters
- web/file/command/screen/app/repo watch rules with configurable timeouts
- inspectable memory with provenance and confidence
- silent meeting records with strict consent defaults
- built-in workflow catalog with approval gates
- audit trail and approval queue

The later Mac app should reuse this core instead of replacing it.

## Safety Defaults

- Screenshots, audio, and transcripts are ephemeral by default.
- Low-risk actions can run automatically.
- Sensitive actions require confirmation.
- Blocked actions, destructive shell commands, credential entry, payments, and
  privacy/security changes are denied by default.
- The kill switch blocks all further automation until the session is resumed.

Sensitive actions include deletion, purchases/payments, credential entry,
message/email sending, software installs, security/privacy changes, and
irreversible system changes.
