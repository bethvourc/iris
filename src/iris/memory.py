from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_memory(
    db: sqlite3.Connection,
    *,
    category: str,
    content: str,
    provenance: str = "manual",
    confidence: float = 1.0,
    user_confirmed: bool = True,
    sensitive: bool = False,
) -> str:
    memory_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        """
        INSERT INTO memories (
          memory_id, category, content, provenance, confidence,
          user_confirmed, sensitive, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            category,
            content,
            provenance,
            confidence,
            int(user_confirmed),
            int(sensitive),
            now,
            now,
        ),
    )
    db.commit()
    try:
        from iris.memory_graph import record_memory_fact

        record_memory_fact(
            db,
            memory_id=memory_id,
            category=category,
            content=content,
            provenance=provenance,
            confidence=confidence,
        )
    except Exception:
        # Flat memory is the compatibility source; graph sync must not break it.
        pass
    return memory_id


def list_memories(
    db: sqlite3.Connection, category: str | None = None
) -> list[dict[str, object]]:
    if category:
        rows = db.execute(
            "SELECT * FROM memories WHERE category = ? ORDER BY updated_at DESC",
            (category,),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def edit_memory(db: sqlite3.Connection, memory_id: str, content: str) -> bool:
    result = db.execute(
        "UPDATE memories SET content = ?, updated_at = ? WHERE memory_id = ?",
        (content, _now(), memory_id),
    )
    db.commit()
    return result.rowcount > 0


def forget_memory(db: sqlite3.Connection, memory_id: str) -> bool:
    result = db.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
    db.commit()
    return result.rowcount > 0
