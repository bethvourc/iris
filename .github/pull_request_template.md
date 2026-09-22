## Summary

<!-- What does this change, and why? Link any related issue. -->

## Testing

<!-- How did you verify it? Which tests did you add or update? -->

## Checklist

- [ ] `uv run ruff check src tests` and `uv run pytest -q` pass
- [ ] Swift changes pass `swiftlint --strict`, `swiftformat --lint .`, and the Xcode tests
- [ ] No secrets, tokens, or personal data in code, tests, fixtures, or logs
- [ ] Docs updated (README, `docs/`, `.env.example`) if behavior or config changed
- [ ] Safety-relevant changes (risk tiers, approvals, gateway auth) are called out above
