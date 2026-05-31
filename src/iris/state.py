from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from iris.config import IrisConfig


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT,
  actor TEXT NOT NULL,
  tool TEXT NOT NULL,
  input_hash TEXT,
  output_hash TEXT,
  risk TEXT NOT NULL,
  approval_id TEXT,
  timestamp TEXT NOT NULL,
  result TEXT NOT NULL,
  error TEXT,
  details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  run_id TEXT,
  action_name TEXT NOT NULL,
  risk TEXT NOT NULL,
  status TEXT NOT NULL,
  preview TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  decided_at TEXT,
  details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS watch_rules (
  watch_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  target TEXT NOT NULL,
  expected TEXT,
  selector TEXT,
  interval_seconds REAL NOT NULL,
  timeout_seconds REAL NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_snapshot TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS watch_runs (
  run_id TEXT PRIMARY KEY,
  watch_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (watch_id) REFERENCES watch_rules(watch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
  memory_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  provenance TEXT NOT NULL,
  confidence REAL NOT NULL,
  user_confirmed INTEGER NOT NULL,
  sensitive INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
  meeting_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  consent_required INTEGER NOT NULL,
  disclosure_spoken INTEGER NOT NULL,
  retention_days INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  stopped_at TEXT,
  transcript_path TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  run_id TEXT PRIMARY KEY,
  workflow_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS rollback_entries (
  rollback_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  inverse_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
  session_id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS agent_messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_tasks (
  task_id TEXT PRIMARY KEY,
  session_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  heartbeat_at TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS agent_task_steps (
  step_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plugin_settings (
  plugin_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL,
  configured INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS connector_settings (
  connector_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL,
  configured INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS knowledge_sources (
  source_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  uri TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS knowledge_pages (
  page_id TEXT PRIMARY KEY,
  source_id TEXT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (source_id) REFERENCES knowledge_sources(source_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS knowledge_links (
  from_page_id TEXT NOT NULL,
  to_page_id TEXT NOT NULL,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (from_page_id, to_page_id, label),
  FOREIGN KEY (from_page_id) REFERENCES knowledge_pages(page_id) ON DELETE CASCADE,
  FOREIGN KEY (to_page_id) REFERENCES knowledge_pages(page_id) ON DELETE CASCADE
);
"""


def ensure_state(config: IrisConfig) -> Path:
    path = config.state_db_path
    if not path.is_absolute():
        path = config.project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        migrate(db)
    return path


@contextmanager
def open_state(config: IrisConfig) -> Iterator[sqlite3.Connection]:
    path = ensure_state(config)
    with connect(path) as db:
        yield db


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


def migrate(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    db.commit()
