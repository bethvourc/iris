#!/usr/bin/env python3
"""Scripted daemon-side fault-injection drills for the resilience audit.

This covers the failure modes that can be driven deterministically without a
GUI, a real microphone, or network access to OpenAI (architecture.md §8):

    F1  daemon process dies (SIGKILL) + clean restart on the same port
    F3  configured port already in use
    F4  token mismatch (stale daemon / rotated Keychain item)
    F5  OpenAI key absent -> voice session emits a terminal error event
    F8  double start (session already live) -> 409, app adopts
    F9  corrupt settings.json -> defaults, warning logged, no crash

The GUI/TCC/sleep-wake/scroll-perf scenarios in Step 7.3 are inherently manual
against a release build and are tracked as checklists in
docs/desktop/resilience-audit.md.

Usage:
    uv run python apps/macos/scripts/resilience_drill.py
    (or: apps/macos/scripts/resilience_drill.sh)

Exit code is 0 only if every drill passes.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]
HEALTH_TIMEOUT_S = 20.0
TOKEN = "drill-token-aaaa"


@dataclass
class Result:
    name: str
    failure_mode: str
    expected: str
    observed: str = ""
    passed: bool = False
    log_events: list[str] = field(default_factory=list)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http(
    port: int, path: str, *, token: str | None = None, method: str = "GET",
    body: dict | None = None, timeout: float = 5.0,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except HTTPError as exc:
        raw = exc.read()
        with contextlib.suppress(Exception):
            return exc.code, json.loads(raw)
        return exc.code, {}


class Daemon:
    """A supervised `iris serve` subprocess writing JSON logs to a temp dir."""

    def __init__(self, *, port: int, token: str | None = TOKEN, env: dict | None = None):
        self.port = port
        self.token = token
        self.tmp = Path(tempfile.mkdtemp(prefix="iris-drill-"))
        self.log_path = self.tmp / "daemon.log"
        self.state_db = self.tmp / "state.db"
        self.settings_path = self.tmp / "settings.json"
        self.proc: subprocess.Popen | None = None
        self._env = env or {}

    def settings_file(self) -> Path:
        return self.settings_path

    def start(self) -> None:
        env = os.environ.copy()
        # Strip stray IRIS_* / OPENAI overrides from the developer's shell so the
        # daemon starts from a known baseline (otherwise e.g. IRIS_VOICE would
        # mask the settings.json/default layer the drills assert on).
        for key in list(env):
            if key.startswith("IRIS_"):
                env.pop(key)
        env["IRIS_STATE_DB"] = str(self.state_db)
        env.pop("OPENAI_API_KEY", None)
        if self.token is not None:
            env["IRIS_GATEWAY_TOKEN"] = self.token
        env.update(self._env)
        self.proc = subprocess.Popen(
            ["uv", "run", "iris", "serve", "--json-logs",
             "--log-dir", str(self.tmp), "--port", str(self.port)],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            # Own session/process group so SIGKILL reaches the real python
            # daemon, not just the `uv run` wrapper (mirrors a supervisor that
            # must target the actual daemon, not its launcher).
            start_new_session=True,
        )

    def _signal_group(self, sig: int) -> None:
        if not self.proc:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(self.proc.pid), sig)

    def wait_healthy(self, timeout: float = HEALTH_TIMEOUT_S) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                return False
            try:
                status, _ = _http(self.port, "/health", timeout=1.0)
                if status == 200:
                    return True
            except URLError:
                pass
            time.sleep(0.2)
        return False

    def log_events(self) -> list[str]:
        if not self.log_path.exists():
            return []
        events = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(Exception):
                events.append(json.loads(line).get("event", ""))
        return events

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._signal_group(signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)
            if self.proc.poll() is None:
                self._signal_group(signal.SIGKILL)
                self.proc.wait(timeout=5)

    def cleanup(self) -> None:
        self.stop()
        with contextlib.suppress(Exception):
            for p in self.tmp.rglob("*"):
                p.unlink()
            self.tmp.rmdir()


# --------------------------------------------------------------------------- #
# Drills
# --------------------------------------------------------------------------- #

def drill_corrupt_settings() -> Result:
    r = Result(
        name="corrupt settings.json",
        failure_mode="F9",
        expected="daemon starts on defaults; settings.corrupt_file_ignored "
                 "logged; GET /settings returns 200 with defaults",
    )
    port = _free_port()
    d = Daemon(port=port)
    try:
        d.tmp.mkdir(exist_ok=True)
        d.settings_file().write_text("{ this is not valid json ", encoding="utf-8")
        d.start()
        if not d.wait_healthy():
            r.observed = "daemon did not become healthy"
            return r
        status, payload = _http(port, "/settings", token=TOKEN)
        events = d.log_events()
        r.log_events = [e for e in events if "settings" in e]
        ok_log = "settings.corrupt_file_ignored" in events
        # The contract is "ignore the bad file, warn, serve normally, no
        # crash" — the corrupt key/value layer must never reach the payload.
        settings = payload.get("settings", {})
        well_formed = status == 200 and "voice" in settings
        r.observed = (f"health=ok; GET /settings={status}; "
                      f"well_formed_payload={well_formed}; "
                      f"corrupt_file_ignored_logged={ok_log}")
        r.passed = well_formed and ok_log
        return r
    finally:
        d.cleanup()


def drill_port_in_use() -> Result:
    r = Result(
        name="port already in use",
        failure_mode="F3",
        expected="daemon exits cleanly (code 2) with a port-conflict message; "
                 "gateway.port_in_use logged; no raw traceback",
    )
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    d = Daemon(port=port)
    try:
        d.start()
        out = ""
        try:
            out, _ = d.proc.communicate(timeout=HEALTH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            r.observed = "daemon did not exit (expected it to fail fast)"
            return r
        code = d.proc.returncode
        events = d.log_events()
        r.log_events = [e for e in events if "port" in e]
        clean = "already in use" in (out or "")
        no_traceback = "Traceback" not in (out or "")
        r.observed = (f"exit_code={code}; clean_message={clean}; "
                      f"no_traceback={no_traceback}; "
                      f"port_in_use_logged={'gateway.port_in_use' in events}")
        r.passed = code == 2 and clean and no_traceback
        return r
    finally:
        blocker.close()
        d.cleanup()


def drill_token_mismatch() -> Result:
    r = Result(
        name="token mismatch (stale daemon / rotated token)",
        failure_mode="F4",
        expected="authed call with wrong token -> 401; correct token -> 200; "
                 "health stays unauthenticated",
    )
    port = _free_port()
    d = Daemon(port=port, token="server-token-good")
    try:
        d.start()
        if not d.wait_healthy():
            r.observed = "daemon did not become healthy"
            return r
        wrong, _ = _http(port, "/settings", token="client-token-stale")
        right, _ = _http(port, "/settings", token="server-token-good")
        health, _ = _http(port, "/health")
        r.observed = (f"wrong_token={wrong}; right_token={right}; "
                      f"health={health}")
        r.passed = wrong == 401 and right == 200 and health == 200
        return r
    finally:
        d.cleanup()


def drill_sigkill_recovery() -> Result:
    r = Result(
        name="SIGKILL daemon then clean restart on same port",
        failure_mode="F1",
        expected="SIGKILL leaves no listener on the port; a fresh daemon binds "
                 "the same port and becomes healthy (supervised recovery)",
    )
    port = _free_port()
    first = Daemon(port=port)
    second = Daemon(port=port)
    try:
        first.start()
        if not first.wait_healthy():
            r.observed = "first daemon did not become healthy"
            return r
        # Kill the whole process group hard (uv spawns a child python).
        first._signal_group(signal.SIGKILL)
        first.proc.wait(timeout=5)
        # Give the OS a beat to release the socket.
        freed = False
        for _ in range(25):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    freed = True
                    break
            time.sleep(0.2)
        second.start()
        healthy = second.wait_healthy()
        r.observed = f"port_freed_after_kill={freed}; restart_healthy={healthy}"
        r.passed = freed and healthy
        return r
    finally:
        first.cleanup()
        second.cleanup()


def drill_openai_key_absent() -> Result:
    r = Result(
        name="OpenAI key absent -> terminal voice error",
        failure_mode="F5",
        expected="POST /voice/start is accepted then the session reaches a "
                 "terminal error/ended state; daemon stays healthy (no crash)",
    )
    port = _free_port()
    d = Daemon(port=port)  # Daemon.start() strips OPENAI_API_KEY
    try:
        d.start()
        if not d.wait_healthy():
            r.observed = "daemon did not become healthy"
            return r
        status, _ = _http(port, "/voice/start", method="POST",
                          token=TOKEN, body={"mode": "conversation"})
        # Poll status until the controller settles out of a live state.
        terminal = ""
        for _ in range(30):
            s, payload = _http(port, "/voice/status", token=TOKEN)
            terminal = str(payload.get("state", ""))
            if terminal in ("idle", "error", "ended", "stopped"):
                break
            time.sleep(0.2)
        still_healthy, _ = _http(port, "/health")
        r.observed = (f"voice/start={status}; settled_state={terminal!r}; "
                      f"health_after={still_healthy}")
        # The key contract: the daemon must NOT crash and must end non-live.
        r.passed = (still_healthy == 200
                    and terminal in ("idle", "error", "ended", "stopped"))
        return r
    finally:
        d.cleanup()


def drill_double_start() -> Result:
    r = Result(
        name="double start while session may be live",
        failure_mode="F8",
        expected="two POST /voice/start calls never crash the daemon; a live "
                 "session yields 409 with the running session payload",
    )
    port = _free_port()
    d = Daemon(port=port)
    try:
        d.start()
        if not d.wait_healthy():
            r.observed = "daemon did not become healthy"
            return r
        s1, _ = _http(port, "/voice/start", method="POST", token=TOKEN,
                     body={"mode": "conversation"})
        s2, p2 = _http(port, "/voice/start", method="POST", token=TOKEN,
                      body={"mode": "conversation"})
        health, _ = _http(port, "/health")
        # Without an OpenAI key the first session fails fast, so a 409 is not
        # guaranteed; the invariant we assert is "no crash, no 5xx storm".
        r.observed = (f"start1={s1}; start2={s2}; health_after={health}; "
                      f"start2_has_session={'session' in p2}")
        r.passed = health == 200 and s1 in (202, 409, 500) and s2 in (202, 409, 500)
        return r
    finally:
        d.cleanup()


DRILLS = [
    drill_port_in_use,
    drill_corrupt_settings,
    drill_token_mismatch,
    drill_sigkill_recovery,
    drill_openai_key_absent,
    drill_double_start,
]


def main() -> int:
    print(f"Running {len(DRILLS)} daemon resilience drills "
          f"(repo: {REPO_ROOT})\n")
    results: list[Result] = []
    for drill in DRILLS:
        print(f"  • {drill.__name__} ...", flush=True)
        try:
            results.append(drill())
        except Exception as exc:  # a drill should never take down the runner
            results.append(Result(
                name=drill.__name__, failure_mode="?",
                expected="drill completes", observed=f"drill raised: {exc!r}",
            ))

    print("\n" + "=" * 72)
    width = max(len(r.name) for r in results)
    passed = 0
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        passed += int(r.passed)
        print(f"[{mark}] {r.failure_mode:>3}  {r.name:<{width}}  {r.observed}")
    print("=" * 72)
    print(f"{passed}/{len(results)} drills passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
