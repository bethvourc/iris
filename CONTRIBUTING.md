# Contributing to Iris

Thanks for your interest in improving Iris. This guide covers local setup, the
checks your change needs to pass, and what we look for in a pull request.

## Ground rules

- **Never commit secrets.** Real config goes in `.env` and `mcp.json`, and both
  are git-ignored. If you add a new setting, add it to `.env.example` with an
  empty or safe default, and never with a real value.
- **Safety changes need extra care.** Iris acts on a real Mac. Any change to
  `src/iris/safety.py`, the approval flow, gateway auth, or a tool's risk tier
  should explain why it's safe, and it should come with tests.
- **Keep the gateway local.** The daemon binds to `127.0.0.1` and requires a
  bearer token on every non-exempt route. Don't widen `AUTH_EXEMPT_PATHS`
  unless you've discussed it in an issue first.
- For large or architectural changes, open an issue first so we can agree on
  the approach before you invest time.

## Python core

```bash
uv sync --dev --no-editable
cp .env.example .env          # OPENAI_API_KEY is only needed for live features
uv run ruff check src tests
uv run pytest -q
```

Tests must run offline and without macOS permissions. Mock network calls and
system APIs rather than calling them. If you add a gateway route, add it to
`_PROTECTED_ROUTES` in `tests/test_gateway.py`. The deny-by-default scan fails
until you do.

## macOS app

```bash
brew install xcodegen swiftlint swiftformat
cd apps/macos
xcodegen generate
swiftlint --strict
swiftformat --lint .
xcodebuild test -project Iris.xcodeproj -scheme Iris \
  -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO
```

`project.yml` is the source of truth for the Xcode project. Edit it rather than
the `.xcodeproj`, run `xcodegen generate`, and commit both.

To sign locally, put your team in `apps/macos/Config/Signing.local.xcconfig`.
That file is git-ignored, and UI tests need it:

```
DEVELOPMENT_TEAM = <your Apple Team ID>
```

If the wire format between the app and the daemon changes, update
[`docs/desktop/api-contract.md`](docs/desktop/api-contract.md) in the same PR.

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Keep each PR focused on one change. Write a clear description of what
   changed and why.
3. Make sure CI passes: Python lint and tests, SwiftLint, SwiftFormat, and the
   Xcode tests.
4. Add or update tests for any behavior you changed.
5. Update the README or `docs/` if you changed user-facing behavior.

`main` is protected. A PR can merge only when every CI check passes and the
maintainer has approved it. Pushing new commits after approval requires a fresh
review. Only the maintainer merges.

Questions about setup or usage are welcome as issues. Pick the
**Question / help** template.

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
