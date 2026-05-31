from __future__ import annotations

from dataclasses import dataclass
import getpass
import json
import os
import re
import sqlite3
import subprocess

from iris.config import IrisConfig
from iris.memory import add_memory


@dataclass(frozen=True)
class UserProfile:
    full_name: str
    preferred_name: str
    pronouns: str | None = None

    def prompt_context(self) -> str:
        pronouns = self.pronouns or "not specified"
        return (
            f"User preferred first name: {self.preferred_name}\n"
            f"User full/system name: {self.full_name}\n"
            f"User pronouns: {pronouns}\n"
            "Address the user by first name naturally, especially in greetings "
            "or confirmations, but do not force their name into every sentence."
        )


def infer_system_profile() -> UserProfile:
    configured_name = os.getenv("IRIS_USER_NAME")
    configured_first = os.getenv("IRIS_USER_FIRST_NAME")
    configured_pronouns = os.getenv("IRIS_USER_PRONOUNS") or None
    full_name = configured_name or _macos_full_name() or getpass.getuser()
    preferred_name = configured_first or _first_name(full_name)
    return UserProfile(
        full_name=full_name,
        preferred_name=preferred_name,
        pronouns=configured_pronouns,
    )


def load_user_profile(config: IrisConfig, db: sqlite3.Connection | None = None) -> UserProfile:
    profile = infer_system_profile()
    if db is None:
        return profile
    rows = db.execute(
        "SELECT content FROM memories WHERE category = 'profile' ORDER BY created_at ASC"
    ).fetchall()
    values: dict[str, str | None] = {
        "full_name": profile.full_name,
        "preferred_name": profile.preferred_name,
        "pronouns": profile.pronouns,
    }
    for row in rows:
        try:
            item = json.loads(str(row["content"]))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            for key in ("full_name", "preferred_name", "pronouns"):
                if item.get(key):
                    values[key] = str(item[key])
    return UserProfile(
        full_name=str(values["full_name"]),
        preferred_name=str(values["preferred_name"]),
        pronouns=str(values["pronouns"]) if values.get("pronouns") else None,
    )


def save_user_profile(
    db: sqlite3.Connection,
    *,
    full_name: str | None = None,
    preferred_name: str | None = None,
    pronouns: str | None = None,
) -> str:
    current = load_user_profile(_dummy_config(), db)
    payload = {
        "full_name": full_name or current.full_name,
        "preferred_name": preferred_name or current.preferred_name,
        "pronouns": pronouns or current.pronouns,
    }
    return add_memory(
        db,
        category="profile",
        content=json.dumps(payload, sort_keys=True),
        provenance="profile command",
        confidence=1.0,
        user_confirmed=True,
        sensitive=False,
    )


def _macos_full_name() -> str | None:
    try:
        completed = subprocess.run(
            ["id", "-F"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    value = completed.stdout.strip()
    return value or None


def _first_name(full_name: str) -> str:
    cleaned = full_name.strip()
    if not cleaned:
        return "there"
    if " " in cleaned:
        return cleaned.split()[0].capitalize()
    pieces = [piece for piece in re.split(r"[._-]+", cleaned) if piece]
    return pieces[0].capitalize() if pieces else cleaned.capitalize()


def _dummy_config() -> IrisConfig:
    return IrisConfig.from_env()
