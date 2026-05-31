from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from iris.config import IrisConfig
from iris.connectors import connector_health, default_manifest_dirs
from iris.memory import add_memory


def collect_machine_context(config: IrisConfig, db: sqlite3.Connection | None = None) -> dict[str, Any]:
    home = Path.home()
    context: dict[str, Any] = {
        "installed_apps": _installed_apps(),
        "project_folders": _project_folders(home),
        "common_folders": _common_folders(home),
        "browser_profiles": _browser_profiles(home),
    }
    if db is not None:
        context["connector_health"] = connector_health(
            db,
            manifest_dirs=default_manifest_dirs(config.project_root),
        )
    return context


def remember_machine_context(config: IrisConfig, db: sqlite3.Connection) -> list[str]:
    context = collect_machine_context(config, db)
    memory_ids = []
    for category, content in _memory_records(context):
        memory_ids.append(
            add_memory(
                db,
                category=category,
                content=content,
                provenance="machine_scan",
                confidence=0.8,
                user_confirmed=False,
                sensitive=False,
            )
        )
    return memory_ids


def _installed_apps(limit: int = 220) -> list[str]:
    apps: set[str] = set()
    for root in (Path("/Applications"), Path.home() / "Applications"):
        if not root.exists():
            continue
        for path in root.glob("*.app"):
            apps.add(path.stem)
        for path in root.glob("*/*.app"):
            apps.add(path.stem)
    return sorted(apps)[:limit]


def _project_folders(home: Path, limit: int = 120) -> list[str]:
    folders: list[str] = []
    for root in (home / "Documents" / "Projects", home / "documents" / "projects"):
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                folders.append(str(path))
            if len(folders) >= limit:
                return folders
    return folders


def _common_folders(home: Path) -> list[str]:
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Documents" / "Projects",
        home / "Library" / "CloudStorage",
    ]
    return [str(path) for path in candidates if path.exists()]


def _browser_profiles(home: Path) -> dict[str, bool]:
    return {
        "chrome_default": (home / "Library" / "Application Support" / "Google" / "Chrome" / "Default").exists(),
        "chrome_profiles": (home / "Library" / "Application Support" / "Google" / "Chrome").exists(),
        "safari": (home / "Library" / "Safari").exists(),
    }


def _memory_records(context: dict[str, Any]) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    if context.get("installed_apps"):
        records.append(("machine_apps", json.dumps(context["installed_apps"], sort_keys=True)))
    if context.get("project_folders"):
        records.append(("machine_projects", json.dumps(context["project_folders"], sort_keys=True)))
    if context.get("common_folders"):
        records.append(("machine_folders", json.dumps(context["common_folders"], sort_keys=True)))
    if context.get("browser_profiles"):
        records.append(("machine_browser", json.dumps(context["browser_profiles"], sort_keys=True)))
    connector_health_value = context.get("connector_health")
    if isinstance(connector_health_value, list):
        concise = [
            {
                "connector_id": item.get("connector_id"),
                "enabled": item.get("enabled"),
                "configured": item.get("configured"),
                "health": item.get("health"),
            }
            for item in connector_health_value
        ]
        records.append(("machine_connectors", json.dumps(concise, sort_keys=True)))
    return records
