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

CREATE TABLE IF NOT EXISTS run_trace_events (
  trace_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  event_name TEXT NOT NULL,
  component TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms REAL,
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

CREATE TABLE IF NOT EXISTS memory_entities (
  entity_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS memory_observations (
  observation_id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  category TEXT NOT NULL,
  confidence REAL NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (source_type, source_id, content)
);

CREATE TABLE IF NOT EXISTS memory_relations (
  relation_id TEXT PRIMARY KEY,
  subject_entity_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_entity_id TEXT,
  object_value TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  confidence REAL NOT NULL,
  provenance TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (subject_entity_id) REFERENCES memory_entities(entity_id) ON DELETE CASCADE,
  FOREIGN KEY (object_entity_id) REFERENCES memory_entities(entity_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS memory_evidence (
  evidence_id TEXT PRIMARY KEY,
  relation_id TEXT NOT NULL,
  observation_id TEXT,
  source_table TEXT NOT NULL,
  source_id TEXT NOT NULL,
  quote TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (relation_id) REFERENCES memory_relations(relation_id) ON DELETE CASCADE,
  FOREIGN KEY (observation_id) REFERENCES memory_observations(observation_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS memory_pending_relations (
  pending_id TEXT PRIMARY KEY,
  observation_id TEXT,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  candidate_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (observation_id) REFERENCES memory_observations(observation_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_entities_kind ON memory_entities(kind);
CREATE INDEX IF NOT EXISTS idx_memory_observations_category ON memory_observations(category);
CREATE INDEX IF NOT EXISTS idx_memory_relations_subject ON memory_relations(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_memory_relations_predicate ON memory_relations(predicate);
CREATE INDEX IF NOT EXISTS idx_memory_relations_active ON memory_relations(active);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_relation ON memory_evidence(relation_id);
CREATE INDEX IF NOT EXISTS idx_memory_pending_relations_source ON memory_pending_relations(source_type, source_id);

CREATE TABLE IF NOT EXISTS memory_embeddings (
  embedding_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  vector_json TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (source_type, source_id, provider, model)
);

CREATE TABLE IF NOT EXISTS memory_retrieval_events (
  event_id TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  source TEXT NOT NULL,
  selected_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_source ON memory_embeddings(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_hash ON memory_embeddings(text_hash);
CREATE INDEX IF NOT EXISTS idx_memory_retrieval_events_created ON memory_retrieval_events(created_at);

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

CREATE TABLE IF NOT EXISTS agent_runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT,
  session_id TEXT,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  current_step TEXT NOT NULL DEFAULT '',
  active_tool TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  approval_id TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id) ON DELETE SET NULL,
  FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE SET NULL
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

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  chunk_id TEXT PRIMARY KEY,
  page_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (page_id, sequence),
  FOREIGN KEY (page_id) REFERENCES knowledge_pages(page_id) ON DELETE CASCADE
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

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_page ON knowledge_chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_hash ON knowledge_chunks(content_hash);
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
    try:
        from iris.memory_graph import backfill_memories

        backfill_memories(db)
    except Exception:
        # State should remain usable even if graph backfill hits legacy data.
        pass
