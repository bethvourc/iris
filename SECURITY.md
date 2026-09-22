# Security Policy

Iris can see your screen, hear your microphone, and operate your Mac, so we
take security reports seriously.

## Reporting a vulnerability

**Please don't open a public issue.** Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/bethvourc/iris/security/advisories/new).

Please include:

- a description of the issue and its impact
- steps to reproduce, or a proof of concept
- the affected version or commit, and your macOS version

We aim to acknowledge reports within a few days. We'll keep you updated while
we work on a fix, and we're happy to credit you in the release notes.

## Supported versions

Iris is pre-1.0. Security fixes go to the latest release and to `main`.

## Scope

These are especially in scope:

- **Safety gate bypasses:** a sensitive or blocked action that runs without
  approval.
- **Gateway exposure:** a route reachable without a valid token, or the daemon
  listening on anything other than `127.0.0.1`.
- **Secret leakage:** API keys or the gateway token showing up in logs,
  diagnostics bundles, API responses, or on disk outside the Keychain or `.env`.
- **Prompt injection:** on-screen or web content that makes Iris take actions
  the user didn't ask for.
- **Code-signing or update integrity** of the macOS app.

Out of scope: vulnerabilities in third-party MCP servers or connectors that you
choose to enable, and attacks that need someone who already has full control of
your user account.
