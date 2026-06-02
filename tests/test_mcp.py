from __future__ import annotations

import json
from pathlib import Path
import sys

from iris.actions import RiskLevel
from iris.mcp import (
    MCPManager,
    MCPServerConfig,
    load_mcp_config,
)
from iris.tools import ToolContext, ToolRegistry, mcp_tool_specs

FAKE_SERVER = str(Path(__file__).parent / "mcp_fake_server.py")


def _fake_server(name: str = "fake") -> MCPServerConfig:
    return MCPServerConfig(name=name, command=sys.executable, args=[FAKE_SERVER])


def test_manager_lists_and_calls_tool() -> None:
    manager = MCPManager([_fake_server()])
    manager.start_all()
    try:
        assert manager.errors == {}
        descriptors = manager.tool_descriptors()
        names = {(d["server"], d["tool"]) for d in descriptors}
        assert ("fake", "echo") in names

        ok, text, _payload = manager.call("fake", "echo", {"text": "hello iris"})
        assert ok
        assert text == "hello iris"
    finally:
        manager.stop_all()


def test_mcp_tools_register_into_registry() -> None:
    manager = MCPManager([_fake_server()])
    manager.start_all()
    try:
        registry = ToolRegistry.default()
        for spec in mcp_tool_specs(manager):
            registry.register(spec)
        tool = registry.get("mcp__fake__echo")
        assert tool is not None
        assert tool.risk == RiskLevel.LOW_RISK

        context = ToolContext(
            controller=object(),  # type: ignore[arg-type]
            perception=object(),  # type: ignore[arg-type]
            safety_gate=object(),  # type: ignore[arg-type]
            openai_client=object(),  # type: ignore[arg-type]
            google_vision=object(),  # type: ignore[arg-type]
        )
        result = tool.execute({"text": "wired up"}, context)
        assert result.ok
        assert result.message == "wired up"
    finally:
        manager.stop_all()


def test_load_mcp_config_reads_enabled_servers(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "enabled_one": {"command": "echo", "args": ["hi"]},
                    "disabled_one": {"command": "echo", "disabled": True},
                }
            }
        )
    )

    class _Cfg:
        project_root = tmp_path

    servers = load_mcp_config(_Cfg())  # type: ignore[arg-type]
    names = {server.name for server in servers}
    assert names == {"enabled_one"}
    assert servers[0].risk == RiskLevel.LOW_RISK


def test_missing_config_returns_no_servers(tmp_path: Path) -> None:
    class _Cfg:
        project_root = tmp_path

    assert load_mcp_config(_Cfg()) == []  # type: ignore[arg-type]
