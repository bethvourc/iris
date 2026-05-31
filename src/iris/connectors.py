from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable


@dataclass(frozen=True)
class ConnectorDescriptor:
    connector_id: str
    name: str
    category: str
    description: str
    tools: tuple[str, ...]
    auth_type: str = "local"
    env_keys: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    risk: str = "low"
    enabled_by_default: bool = True
    built_in: bool = True
    metadata: dict[str, Any] | None = None


LOCAL_READY_AUTH_TYPES = {"none", "local", "mac_permission", "browser_session", "local_app"}


def default_manifest_dirs(project_root: Path | None = None) -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    dirs = [repo_root / "connectors"]
    if project_root is not None:
        dirs.append(project_root / "connectors")
    dirs.append(Path.home() / ".iris" / "connectors")
    unique: list[Path] = []
    for path in dirs:
        resolved = path.expanduser()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def load_connectors(
    manifest_dirs: Iterable[Path] | None = None,
) -> tuple[ConnectorDescriptor, ...]:
    descriptors: dict[str, ConnectorDescriptor] = {}
    for manifest_dir in manifest_dirs or default_manifest_dirs():
        if not manifest_dir.exists():
            continue
        for path in sorted(manifest_dir.glob("*.json")):
            for descriptor in _load_manifest_file(path):
                descriptors[descriptor.connector_id] = descriptor
    return tuple(descriptors.values())


def list_connectors(
    db: sqlite3.Connection,
    *,
    manifest_dirs: Iterable[Path] | None = None,
    category: str | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    descriptors = load_connectors(manifest_dirs)
    _ensure_rows(db, descriptors)
    settings = _settings(db)
    result: list[dict[str, Any]] = []
    for descriptor in descriptors:
        if category and descriptor.category != category:
            continue
        item = _connector_item(descriptor, settings.get(descriptor.connector_id))
        if enabled_only and not item["enabled"]:
            continue
        result.append(item)
    return result


def connector_health(
    db: sqlite3.Connection,
    *,
    manifest_dirs: Iterable[Path] | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "connector_id": item["connector_id"],
            "name": item["name"],
            "category": item["category"],
            "enabled": item["enabled"],
            "configured": item["configured"],
            "health": item["health"],
            "missing_env": item["missing_env"],
            "tools": item["tools"],
        }
        for item in list_connectors(db, manifest_dirs=manifest_dirs)
    ]


def get_connector(
    db: sqlite3.Connection,
    connector_id_or_name: str,
    *,
    manifest_dirs: Iterable[Path] | None = None,
) -> dict[str, Any] | None:
    descriptors = load_connectors(manifest_dirs)
    _ensure_rows(db, descriptors)
    normalized = _normalize(connector_id_or_name)
    descriptor = next(
        (
            item
            for item in descriptors
            if normalized in {_normalize(item.connector_id), _normalize(item.name)}
        ),
        None,
    )
    if descriptor is None:
        return None
    return _connector_item(descriptor, _settings(db).get(descriptor.connector_id))


def set_connector_enabled(
    db: sqlite3.Connection,
    connector_id: str,
    enabled: bool,
    *,
    manifest_dirs: Iterable[Path] | None = None,
) -> bool:
    descriptors = load_connectors(manifest_dirs)
    descriptor = next(
        (item for item in descriptors if _normalize(item.connector_id) == _normalize(connector_id)),
        None,
    )
    if descriptor is None:
        return False
    db.execute(
        """
        INSERT INTO connector_settings (connector_id, enabled, configured, updated_at, config_json)
        VALUES (?, ?, ?, ?, '{}')
        ON CONFLICT(connector_id) DO UPDATE SET
          enabled = excluded.enabled,
          configured = excluded.configured,
          updated_at = excluded.updated_at
        """,
        (
            descriptor.connector_id,
            1 if enabled else 0,
            1 if _is_configured(descriptor) else 0,
            _now(),
        ),
    )
    db.commit()
    return True


def connector_categories(
    manifest_dirs: Iterable[Path] | None = None,
) -> list[str]:
    return sorted({connector.category for connector in load_connectors(manifest_dirs)})


def _load_manifest_file(path: Path) -> list[ConnectorDescriptor]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("connectors", [raw])
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    descriptors: list[ConnectorDescriptor] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        connector_id = str(item.get("id") or item.get("connector_id") or "").strip()
        if not connector_id:
            continue
        descriptors.append(
            ConnectorDescriptor(
                connector_id=connector_id,
                name=str(item.get("name") or connector_id),
                category=str(item.get("category") or "custom"),
                description=str(item.get("description") or ""),
                tools=tuple(str(tool) for tool in item.get("tools", [])),
                auth_type=str(item.get("auth_type") or "local"),
                env_keys=tuple(str(key) for key in item.get("env_keys", [])),
                scopes=tuple(str(scope) for scope in item.get("scopes", [])),
                risk=str(item.get("risk") or "low"),
                enabled_by_default=bool(item.get("enabled_by_default", True)),
                built_in=bool(item.get("built_in", path.parent.name == "connectors")),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
            )
        )
    return descriptors


def _connector_item(
    descriptor: ConnectorDescriptor,
    setting: sqlite3.Row | None,
) -> dict[str, Any]:
    item = asdict(descriptor)
    item["tools"] = list(descriptor.tools)
    item["env_keys"] = list(descriptor.env_keys)
    item["scopes"] = list(descriptor.scopes)
    item["enabled"] = bool(setting["enabled"]) if setting else descriptor.enabled_by_default
    item["configured"] = _is_configured(descriptor)
    item["missing_env"] = _missing_env(descriptor)
    item["updated_at"] = setting["updated_at"] if setting else None
    if not item["enabled"]:
        item["health"] = "disabled"
    elif item["configured"]:
        item["health"] = "ready"
    else:
        item["health"] = "needs_config"
    return item


def _settings(db: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = db.execute(
        "SELECT connector_id, enabled, configured, updated_at, config_json FROM connector_settings"
    ).fetchall()
    return {row["connector_id"]: row for row in rows}


def _ensure_rows(
    db: sqlite3.Connection,
    descriptors: Iterable[ConnectorDescriptor],
) -> None:
    now = _now()
    for descriptor in descriptors:
        db.execute(
            """
            INSERT OR IGNORE INTO connector_settings (
              connector_id, enabled, configured, updated_at, config_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                descriptor.connector_id,
                1 if descriptor.enabled_by_default else 0,
                1 if _is_configured(descriptor) else 0,
                now,
                json.dumps({}, sort_keys=True),
            ),
        )
    db.commit()


def _is_configured(descriptor: ConnectorDescriptor) -> bool:
    if descriptor.auth_type in LOCAL_READY_AUTH_TYPES:
        return True
    if not descriptor.env_keys:
        return False
    return any(os.getenv(key) for key in descriptor.env_keys)


def _missing_env(descriptor: ConnectorDescriptor) -> list[str]:
    if descriptor.auth_type in LOCAL_READY_AUTH_TYPES:
        return []
    return [key for key in descriptor.env_keys if not os.getenv(key)]


def _normalize(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
