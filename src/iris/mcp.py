"""Minimal Model Context Protocol (MCP) client.

Iris speaks MCP over stdio (newline-delimited JSON-RPC 2.0) with no external
dependency, in the same hand-rolled spirit as the Chrome DevTools client. This
lets the agent pick up tools from any configured MCP server (filesystem, web
search, Spotify, GitHub, Slack, ...) without writing a Python tool for each —
the whole point of not hardcoding capabilities.

Configure servers in mcp.json at the project root (or the path in
IRIS_MCP_CONFIG):

    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/Documents"],
          "env": {},
          "disabled": false,
          "risk": "low_risk"
        }
      }
    }
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

from iris.actions import RiskLevel
from iris.config import IrisConfig

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """An MCP server returned a JSON-RPC error or failed to respond."""


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    risk: RiskLevel = RiskLevel.LOW_RISK


def load_mcp_config(config: IrisConfig | None) -> list[MCPServerConfig]:
    """Read enabled MCP servers from mcp.json (or IRIS_MCP_CONFIG)."""
    path = _config_path(config)
    if path is None or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return []
    servers_raw = raw.get("mcpServers")
    if not isinstance(servers_raw, dict):
        return []
    servers: list[MCPServerConfig] = []
    for name, entry in servers_raw.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("disabled") is True or entry.get("enabled") is False:
            continue
        command = str(entry.get("command") or "").strip()
        if not command:
            continue
        args = [str(item) for item in entry.get("args", []) if str(item)]
        env = {
            str(key): str(value)
            for key, value in (entry.get("env") or {}).items()
        }
        risk = _parse_risk(entry.get("risk"))
        servers.append(
            MCPServerConfig(
                name=str(name),
                command=command,
                args=args,
                env=env,
                cwd=str(entry["cwd"]) if entry.get("cwd") else None,
                risk=risk,
            )
        )
    return servers


def _config_path(config: IrisConfig | None) -> Path | None:
    override = os.environ.get("IRIS_MCP_CONFIG")
    if override:
        return Path(override).expanduser()
    if config is not None:
        return config.project_root / "mcp.json"
    return None


def _parse_risk(value: Any) -> RiskLevel:
    text = str(value or "").strip().lower()
    if text in {"sensitive", "blocked", "low_risk"}:
        return RiskLevel(text)
    return RiskLevel.LOW_RISK


class MCPClient:
    """One stdio MCP server connection."""

    def __init__(self, server: MCPServerConfig, *, init_timeout: float = 20.0) -> None:
        self.server = server
        self.init_timeout = init_timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._responses: dict[int, dict[str, Any]] = {}
        self._next = 1
        self._closed = False
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self.tools: list[dict[str, Any]] = []
        self.error: str | None = None

    def start(self) -> bool:
        env = {**os.environ, **self.server.env}
        try:
            self._proc = subprocess.Popen(
                [self.server.command, *self.server.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=self.server.cwd or None,
            )
        except Exception as exc:
            self.error = f"failed to launch: {exc}"
            return False
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "iris", "version": "0.1.0"},
                },
                timeout=self.init_timeout,
            )
            self._notify("notifications/initialized", {})
            result = self._request("tools/list", {}, timeout=self.init_timeout)
            tools = result.get("tools")
            self.tools = [t for t in tools if isinstance(t, dict)] if isinstance(
                tools, list
            ) else []
            return True
        except Exception as exc:
            self.error = f"{exc}{self._stderr_hint()}"
            self.stop()
            return False

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float = 120.0
    ) -> dict[str, Any]:
        return self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )

    def stop(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                continue
            if isinstance(message, dict) and message.get("id") is not None:
                with self._cond:
                    self._responses[message["id"]] = message
                    self._cond.notify_all()
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.strip()
            if text:
                self._stderr_tail.append(text)

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError("server is not running")
        with self._lock:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        with self._cond:
            request_id = self._next
            self._next += 1
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        deadline = time.monotonic() + timeout
        with self._cond:
            while request_id not in self._responses:
                if self._closed:
                    raise MCPError(f"server closed before answering {method}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPError(f"timed out waiting for {method}")
                self._cond.wait(remaining)
            message = self._responses.pop(request_id)
        if "error" in message and message["error"]:
            raise MCPError(str(message["error"]))
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _stderr_hint(self) -> str:
        if not self._stderr_tail:
            return ""
        return " | stderr: " + " ".join(list(self._stderr_tail)[-3:])


class MCPManager:
    """Starts configured MCP servers and routes tool calls to them."""

    def __init__(self, servers: list[MCPServerConfig]) -> None:
        self._servers = servers
        self._clients: dict[str, MCPClient] = {}
        self.errors: dict[str, str] = {}

    def start_all(self) -> None:
        for server in self._servers:
            client = MCPClient(server)
            if client.start():
                self._clients[server.name] = client
            else:
                self.errors[server.name] = client.error or "unknown error"

    def tool_descriptors(self) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        for name, client in self._clients.items():
            for tool in client.tools:
                tool_name = str(tool.get("name") or "").strip()
                if not tool_name:
                    continue
                descriptors.append(
                    {
                        "server": name,
                        "tool": tool_name,
                        "description": str(tool.get("description") or ""),
                        "input_schema": tool.get("inputSchema")
                        or tool.get("input_schema"),
                        "risk": client.server.risk,
                    }
                )
        return descriptors

    def call(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> tuple[bool, str, Any]:
        client = self._clients.get(server)
        if client is None:
            return False, f"MCP server '{server}' is not connected.", None
        try:
            result = client.call_tool(tool, arguments)
        except Exception as exc:
            return False, f"MCP tool {server}.{tool} failed: {exc}", None
        text = _content_to_text(result.get("content"))
        is_error = bool(result.get("isError"))
        if not text:
            text = "Done." if not is_error else f"{server}.{tool} reported an error."
        return (not is_error), text, result

    def stop_all(self) -> None:
        for client in self._clients.values():
            client.stop()
        self._clients.clear()


def _content_to_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text"):
            parts.append(str(item["text"]))
        elif item.get("type") == "resource":
            resource = item.get("resource")
            if isinstance(resource, dict) and resource.get("text"):
                parts.append(str(resource["text"]))
    return "\n".join(parts).strip()
