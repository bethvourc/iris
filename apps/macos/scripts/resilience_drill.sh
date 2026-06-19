#!/usr/bin/env bash
# Run the daemon-side resilience drills (Step 7.3 / docs/desktop/resilience-audit.md).
# Drives `iris serve` subprocesses through scripted fault injection and prints a
# PASS/FAIL table. Exit code is non-zero if any drill fails.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
exec uv run python apps/macos/scripts/resilience_drill.py "$@"
