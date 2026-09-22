# Iris

**A local-first voice and vision agent for macOS.**

[![CI](https://github.com/bethvourc/iris/actions/workflows/ci.yml/badge.svg)](https://github.com/bethvourc/iris/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: macOS](https://img.shields.io/badge/platform-macOS%2014%2B-lightgrey.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

Iris sees your screen, listens when you ask, and operates your Mac and browser
on your behalf: opening apps, reading pages, clicking through UIs, playing
media, drafting messages. Every action passes through a safety gate first.
It runs as a terminal CLI or as a native menu-bar app. State, memory, and audit
logs stay on your machine.

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Safety model](#safety-model)
- [macOS app](#macos-app)
- [Extending Iris](#extending-iris)
- [Development](#development)
- [Documentation](#documentation)
- [Contributing](#contributing) · [Security](#security) · [License](#license)

## Features

- **Voice and text.** Live speech-to-speech over the OpenAI Realtime API, with
  wake words (`iris`, `hey iris`), barge-in, and echo suppression. There's also
  a plain text REPL for quiet environments.
- **Screen awareness.** Periodic screen capture and description, plus optional
  Google Cloud Vision OCR.
- **Mac and browser control.** Native UI automation through the macOS
  Accessibility API, Chrome via the DevTools Protocol, and a computer-use model
  as a fallback.
- **Safety first.** Every action is classified as *low-risk*, *sensitive*, or
  *blocked* before it runs. A global kill-switch hotkey pauses all automation,
  and an audit trail records what happened.
- **Local memory.** A SQLite-backed memory graph with offline embeddings by
  default, and a knowledge base you can ingest folders of notes into.
- **Extensible.** Connectors are JSON manifests, not code. Iris also speaks
  [MCP](https://modelcontextprotocol.io), and ships recipes, watchers,
  workflows, and evals.
- **Native desktop app.** A SwiftUI menu-bar app with a global hotkey, a
  Siri-style voice overlay, an activity feed, and approvals. It's backed by the
  same Python core.

## How it works

```
┌──────────────────────────────┐        ┌──────────────────────────────────┐
│  Iris.app (Swift / SwiftUI)  │  HTTP  │  Python core (`iris serve`)      │
│  menu bar · hotkey · overlay │ ◄────► │  agent loop · tools · safety     │
│  activity · approvals        │  SSE   │  memory · connectors · audit     │
└──────────────────────────────┘        └───────────────┬──────────────────┘
        127.0.0.1 only · bearer token in Keychain       │
                                                        ▼
                               OpenAI · Groq · Accessibility · Chrome CDP
```

The Python core does all the reasoning and holds all the state. You can drive
it directly from the terminal (`./iris start`), or let the macOS app supervise
it as a local daemon. The gateway binds to `127.0.0.1` only, and every route
except `/health` and the static UI shells requires a bearer token.

For the full design, including trust boundaries and failure modes, see
[`docs/desktop/architecture.md`](docs/desktop/architecture.md).

## Quick start

**Requirements:** macOS, Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and
an OpenAI API key.

```bash
git clone https://github.com/bethvourc/iris.git
cd iris
uv sync --dev --no-editable
cp .env.example .env          # then set OPENAI_API_KEY in .env
./iris init                   # create local state
./iris setup                  # personalize: your name, pronouns, preferences
./iris permissions            # check which macOS permissions are missing
```

Grant the terminal app that runs Iris these permissions in **System Settings →
Privacy & Security**, then restart Iris:

- Microphone
- Screen Recording
- Accessibility
- Automation

Start a session:

```bash
./iris start          # text mode
./iris start --live   # live voice mode
```

Inside a session you can use `/listen [seconds]`, `/reset`, `kill`, `resume`,
and `quit`.

Run `./iris doctor` whenever something doesn't work. It checks permissions,
providers, connectors, and control backends.

## Configuration

All configuration comes from environment variables, loaded from `.env`.
[`.env.example`](.env.example) documents every option. Only `OPENAI_API_KEY`
is required.

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | **Required.** Realtime voice, reasoning, vision, computer-use. |
| `IRIS_GATEWAY_TOKEN` | Bearer token for the local HTTP gateway (`./iris serve`). |
| `GROQ_API_KEY` | Optional. Low-latency STT and intent routing; falls back to OpenAI. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional. Service-account JSON path for Google Vision OCR. |
| `PUSHOVER_TOKEN`, `PUSHOVER_USER` | Optional. Phone push notifications (or use `ntfy`). |
| `RESEND_API_KEY` | Optional. Outbound email for meeting recaps and `send_email`. |
| `IRIS_*_MODEL` | Model routing overrides. Run `./iris providers` to see the active routes. |

To use the gateway, generate a token:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Local state is stored in SQLite at `build/iris.sqlite3` by default. You can
change this with `IRIS_STATE_DB`.

## Usage

<details>
<summary><b>Status and diagnostics</b></summary>

```bash
./iris status
./iris providers
./iris doctor
./iris test-screen
./iris test-vision
./iris test-voice --listen
./iris notify test
./iris diagnostics last-run
```
</details>

<details>
<summary><b>Mac and browser control</b></summary>

```bash
./iris control health
./iris controls apps
./iris controls inspect
./iris controls find "search"
./iris browser start-cdp
./iris browser tabs
./iris browser current
```
</details>

<details>
<summary><b>Memory and knowledge</b></summary>

```bash
./iris memory add --category project --content "Iris is local-first."
./iris memory search "spotify preferences"
./iris memory related "Iris"
./iris memory explain "spotify"
./iris knowledge ingest-folder ~/Documents/Notes
./iris knowledge search "agent runtime"
```

With the gateway running, the visual memory browser is available at
<http://127.0.0.1:8765/memory-browser>.
</details>

<details>
<summary><b>Background runtime, tasks, and approvals</b></summary>

```bash
./iris serve
./iris sessions list
./iris tasks list
./iris tasks show <task_id>
./iris tasks run-next
./iris tasks cancel <task_id>
./iris tasks resume <task_id>
./iris approvals
./iris approve <id>
./iris deny <id>
./iris audit
```
</details>

<details>
<summary><b>Connectors, recipes, watchers, and evals</b></summary>

```bash
./iris connectors list
./iris connectors health
./iris connectors enable google-ads
./iris recipes list
./iris recipes show spotify_play_song
./iris watch add --kind web --name "Example" --target https://example.com --expected Example
./iris watch list
./iris evals list
./iris evals run
```
</details>

Run `./iris --help` or `./iris <command> --help` to see every command.

## Safety model

Before an action runs, Iris classifies it into one of three tiers:

| Tier | Behavior | Examples |
|------|----------|----------|
| **Low-risk** | Runs automatically | Reading the screen, opening an app, browsing |
| **Sensitive** | Waits for your explicit approval | Sending messages or email, running shell commands, editing or moving files, installing software, creating calendar events |
| **Blocked** | Refused | Payments, entering credentials, changing security or privacy settings, deleting files, destructive shell commands (`rm -rf`, `sudo`, `git reset --hard`, …) |

The kill-switch hotkey (`IRIS_KILL_HOTKEY`, default <kbd>⌃</kbd><kbd>⌥</kbd><kbd>K</kbd>)
pauses all automation immediately. Actions are recorded in a local audit trail
(`./iris audit`). Meeting mode requires your consent by default
(`IRIS_MEETING_CONSENT_REQUIRED`).

## macOS app

The native app lives in [`apps/macos/`](apps/macos). It spawns and supervises
the Python daemon, with health polling, restart with backoff, and crash-loop
detection. It keeps the gateway token in the Keychain and passes it to the
daemon through the environment, so the token is never written to disk. Shipped
builds store state in `~/Library/Application Support/Iris/` and logs in
`~/Library/Logs/Iris/`.

**Build from source** (requires Xcode 16+ and macOS 14+):

```bash
brew install xcodegen           # the project is generated from project.yml
cd apps/macos
xcodegen generate
open Iris.xcodeproj
```

To run the tests without signing:

```bash
xcodebuild test -project Iris.xcodeproj -scheme Iris \
  -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO
```

To sign locally (UI tests need this), create
`apps/macos/Config/Signing.local.xcconfig`. The file is git-ignored.

```
DEVELOPMENT_TEAM = <your Apple Team ID>
```

If you fork the project, you'll also need to change the `com.bethvour.*` bundle
identifiers in `project.yml` to your own.

Signed, notarized DMG releases are produced by
[`release.yml`](.github/workflows/release.yml). See
[`docs/desktop/release.md`](docs/desktop/release.md) for how that works.

## Extending Iris

**Connectors** are JSON manifests. Iris loads them from `connectors/` and
`~/.iris/connectors/`:

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

**MCP servers:** copy [`mcp.json.example`](mcp.json.example) to `mcp.json`,
which is git-ignored, and enable the servers you want. Keep tokens in `.env`
and reference them as `${VAR}`. Don't paste tokens inline.

**Recipes** (`recipes/builtin.json`) are reusable, multi-step action plans.
**Evals** (`evals/agent_tasks.json`) check that requests get routed to the
right tools.

## Development

```bash
uv sync --dev --no-editable
uv run ruff check src tests     # lint
uv run pytest -q                # tests
```

CI runs on every pull request. It runs Python lint and tests, SwiftLint and
SwiftFormat, the Xcode unit tests, and an ad-hoc-signed dry run of the release
pipeline.

```
src/iris/          Python core: agent loop, tools, safety, memory, gateway, CLI
tests/             pytest suite
apps/macos/        SwiftUI app (Iris), shared framework (IrisKit), tests, release scripts
connectors/        built-in connector manifests
recipes/           built-in action recipes
evals/             agent capability eval cases
docs/desktop/      architecture, API contract, packaging, release, runbook
```

## Documentation

| Document | What's in it |
|----------|--------------|
| [Architecture](docs/desktop/architecture.md) | Components, trust boundaries, failure modes |
| [API contract](docs/desktop/api-contract.md) | Gateway routes and the SSE event catalog |
| [Packaging](docs/desktop/packaging.md) | The embedded, relocatable Python runtime |
| [Release](docs/desktop/release.md) | Signing, notarization, DMG, and rollback |
| [Runbook](docs/desktop/runbook.md) | Logs, health checks, reset, uninstall, and failure signatures |
| [Resilience audit](docs/desktop/resilience-audit.md) | Fault-injection drills and their results |

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before you
open a pull request.

## Security

Please don't open public issues for vulnerabilities. See
[SECURITY.md](SECURITY.md) for how to report one privately.

## License

[MIT](LICENSE)
