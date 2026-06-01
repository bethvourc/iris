# Iris

Iris is a local-first Mac agent that runs from the terminal. It supports text,
voice, live screen awareness, safe Mac/browser actions, local state, connectors,
watchers, approvals, and audit logs.

## Requirements

- macOS
- Python 3.11+
- `uv`
- OpenAI API key

Optional:

- Google Vision credentials for OCR/extra screen reading
- Pushover credentials for phone notifications

## Setup

```bash
cd /Users/clintonimaro/Documents/Projects/iris
uv sync --dev --no-editable
cp .env.example .env
```

Edit `.env` and add at least:

```bash
OPENAI_API_KEY=...
```

Optional phone alerts:

```bash
IRIS_NOTIFY_PROVIDER=pushover
PUSHOVER_TOKEN=...
PUSHOVER_USER=...
```

Initialize local state:

```bash
./iris init
./iris status
```

Personalize Iris:

```bash
./iris setup
```

For non-interactive setup:

```bash
./iris setup --name Clinton --pronouns he/him
```

If you use the local HTTP gateway, set a private token first:

```bash
IRIS_GATEWAY_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
```

## macOS Permissions

Run:

```bash
./iris permissions
```

Then grant the terminal app running Iris these permissions in macOS System
Settings:

- Microphone
- Screen Recording
- Accessibility
- Automation

Restart Iris after changing permissions.

## Start Iris

Text mode:

```bash
./iris start
```

Live voice mode:

```bash
./iris start --live
```

Wake words default to:

```text
iris, hey iris
```

Inside `./iris start`, useful commands:

```text
/listen
/listen 1.5
/reset
kill
resume
quit
```

## Common Commands

```bash
./iris status
./iris providers
./iris profile show
./iris profile set --name Clinton --pronouns he/him
./iris setup
./iris test-screen
./iris test-vision
./iris test-voice --listen
./iris control health
./iris doctor
./iris browser start-cdp
./iris browser tabs
./iris browser current
./iris controls apps
./iris controls inspect
./iris controls find "search"
./iris recipes list
./iris recipes show spotify_play_song
./iris notify test
./iris approvals
./iris audit
```

Watchers:

```bash
./iris watch add --kind web --name "Example" --target https://example.com --expected Example
./iris watch list
```

Memory and knowledge:

```bash
./iris memory add --category project --content "Iris is local-first."
./iris knowledge ingest-folder ~/Documents/Notes
./iris knowledge search "agent runtime"
```

Connectors:

```bash
./iris connectors list
./iris connectors health
./iris connectors show stripe
./iris connectors enable google-ads
```

Agent evals:

```bash
./iris evals list
./iris evals run
```

Background runtime:

```bash
./iris serve
./iris sessions list
./iris tasks list
./iris tasks show <task_id>
./iris tasks run-next
./iris tasks cancel <task_id>
./iris tasks resume <task_id>
```

Gateway API routes other than `/health` require:

```text
Authorization: Bearer <IRIS_GATEWAY_TOKEN>
```

## Connector Manifests

Connectors are JSON manifests, not router code. Add connector files under:

```text
connectors/
~/.iris/connectors/
```

Example:

```json
{
  "id": "stripe",
  "name": "Stripe",
  "category": "business",
  "description": "Open dashboards, read revenue, and monitor payments.",
  "tools": ["browser_open", "browser_extract", "integration_status"],
  "auth_type": "api_key",
  "env_keys": ["STRIPE_API_KEY", "STRIPE_DASHBOARD_URL"],
  "risk": "private",
  "enabled_by_default": false
}
```

## Notes

- Low-risk actions can run automatically.
- Sensitive actions require approval.
- Destructive, credential, payment, and security actions are blocked or gated.
- Local state is stored in SQLite under `build/` by default.
