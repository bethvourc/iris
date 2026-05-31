from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any


@dataclass(frozen=True)
class PluginDescriptor:
    plugin_id: str
    name: str
    category: str
    description: str
    tools: tuple[str, ...]
    built_in: bool = True
    enabled_by_default: bool = True


BUILTIN_PLUGINS: tuple[PluginDescriptor, ...] = (
    PluginDescriptor(
        "browser",
        "Browser Control",
        "computer",
        "Control and inspect browser tabs through generic browser tools.",
        ("browser_open", "browser_current_page", "browser_extract", "browser_click", "browser_type"),
    ),
    PluginDescriptor(
        "mac-apps",
        "Mac App Control",
        "computer",
        "Open apps, activate windows, press hotkeys, type, click, and scroll.",
        ("app_open", "app_activate", "app_hotkey", "screen_click_element"),
    ),
    PluginDescriptor(
        "media",
        "Media Control",
        "media",
        "Search, play, pause, and inspect current media state.",
        ("media_search", "media_play", "media_pause", "audio_current_media"),
    ),
    PluginDescriptor(
        "gmail",
        "Gmail",
        "private-app",
        "Search and summarize Gmail with approval gates.",
        ("gmail_search", "gmail_search_and_summarize"),
        enabled_by_default=False,
    ),
    PluginDescriptor(
        "files",
        "Files",
        "filesystem",
        "Find, open, and summarize local files with safety gates.",
        ("file_find", "file_open", "file_summarize"),
    ),
    PluginDescriptor(
        "workflows",
        "Workflow Playbooks",
        "workflow",
        "Run reusable Iris workflows with approvals and done conditions.",
        ("workflow_run",),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_plugins(db: sqlite3.Connection) -> list[dict[str, Any]]:
    _ensure_builtin_rows(db)
    rows = db.execute(
        "SELECT plugin_id, enabled, configured, updated_at, config_json FROM plugin_settings"
    ).fetchall()
    settings = {row["plugin_id"]: row for row in rows}
    result: list[dict[str, Any]] = []
    for descriptor in BUILTIN_PLUGINS:
        setting = settings.get(descriptor.plugin_id)
        item = asdict(descriptor)
        item["tools"] = list(descriptor.tools)
        item["enabled"] = bool(setting["enabled"]) if setting else descriptor.enabled_by_default
        item["configured"] = bool(setting["configured"]) if setting else True
        item["updated_at"] = setting["updated_at"] if setting else None
        item["health"] = "enabled" if item["enabled"] else "disabled"
        result.append(item)
    return result


def set_plugin_enabled(db: sqlite3.Connection, plugin_id: str, enabled: bool) -> bool:
    descriptor = _descriptor(plugin_id)
    if descriptor is None:
        return False
    db.execute(
        """
        INSERT INTO plugin_settings (plugin_id, enabled, configured, updated_at, config_json)
        VALUES (?, ?, 1, ?, '{}')
        ON CONFLICT(plugin_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at
        """,
        (plugin_id, 1 if enabled else 0, _now()),
    )
    db.commit()
    return True


def plugin_health(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "plugin_id": item["plugin_id"],
            "name": item["name"],
            "enabled": item["enabled"],
            "configured": item["configured"],
            "health": item["health"],
            "tools": item["tools"],
        }
        for item in list_plugins(db)
    ]


def _ensure_builtin_rows(db: sqlite3.Connection) -> None:
    now = _now()
    for descriptor in BUILTIN_PLUGINS:
        db.execute(
            """
            INSERT OR IGNORE INTO plugin_settings (
              plugin_id, enabled, configured, updated_at, config_json
            ) VALUES (?, ?, 1, ?, ?)
            """,
            (
                descriptor.plugin_id,
                1 if descriptor.enabled_by_default else 0,
                now,
                json.dumps({}, sort_keys=True),
            ),
        )
    db.commit()


def _descriptor(plugin_id: str) -> PluginDescriptor | None:
    return next((plugin for plugin in BUILTIN_PLUGINS if plugin.plugin_id == plugin_id), None)
