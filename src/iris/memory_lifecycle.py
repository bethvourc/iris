from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import sqlite3
from typing import Any


@dataclass(frozen=True)
class LifecycleMaintenanceResult:
    relations: int
    updated: int
    pinned: int
    stale: int


def ensure_lifecycle(db: sqlite3.Connection, relation_id: str) -> None:
    now = _now()
    exists = db.execute(
        "SELECT relation_id FROM memory_lifecycle WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()
    if exists:
        return
    db.execute(
        """
        INSERT INTO memory_lifecycle
        (relation_id, last_accessed_at, access_count, decay_score, expires_at,
         pinned, review_status, created_at, updated_at, metadata_json)
        VALUES (?, NULL, 0, 1.0, NULL, 0, 'active', ?, ?, '{}')
        """,
        (relation_id, now, now),
    )


def ensure_all_lifecycle(db: sqlite3.Connection) -> int:
    rows = db.execute("SELECT relation_id FROM memory_relations").fetchall()
    for row in rows:
        ensure_lifecycle(db, str(row["relation_id"]))
    db.commit()
    return len(rows)


def maintain_lifecycle(db: sqlite3.Connection) -> LifecycleMaintenanceResult:
    ensure_all_lifecycle(db)
    rows = db.execute(
        """
        SELECT r.relation_id, r.predicate, r.active, r.provenance, r.confidence,
               r.updated_at AS relation_updated_at, l.*
        FROM memory_relations r
        JOIN memory_lifecycle l ON l.relation_id = r.relation_id
        """
    ).fetchall()
    updated = 0
    pinned = 0
    stale = 0
    now = _utcnow()
    for row in rows:
        is_pinned = bool(row["pinned"])
        if is_pinned:
            score = 1.0
            pinned += 1
        else:
            age_days = _age_days(str(row["relation_updated_at"]), now)
            half_life = _half_life_days(
                predicate=str(row["predicate"]),
                provenance=str(row["provenance"]),
                active=bool(row["active"]),
            )
            score = math.exp(-age_days / half_life)
            score += _access_boost(row, now)
            score = max(0.03, min(score, 1.0))
            if not bool(row["active"]):
                score = min(score, 0.35)
        review_status = str(row["review_status"] or "active")
        if score < 0.18 and review_status == "active":
            review_status = "stale"
        if review_status == "stale":
            stale += 1
        if (
            abs(float(row["decay_score"]) - score) > 0.0001
            or review_status != row["review_status"]
        ):
            db.execute(
                """
                UPDATE memory_lifecycle
                SET decay_score = ?, review_status = ?, updated_at = ?
                WHERE relation_id = ?
                """,
                (score, review_status, _now(), row["relation_id"]),
            )
            updated += 1
    db.commit()
    return LifecycleMaintenanceResult(
        relations=len(rows),
        updated=updated,
        pinned=pinned,
        stale=stale,
    )


def lifecycle_by_relation(
    db: sqlite3.Connection, relation_ids: list[str] | set[str] | None = None
) -> dict[str, dict[str, Any]]:
    ensure_all_lifecycle(db)
    if relation_ids:
        placeholders = ",".join("?" for _ in relation_ids)
        rows = db.execute(
            f"SELECT * FROM memory_lifecycle WHERE relation_id IN ({placeholders})",
            tuple(relation_ids),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM memory_lifecycle").fetchall()
    return {str(row["relation_id"]): dict(row) for row in rows}


def record_memory_access(db: sqlite3.Connection, relation_ids: list[str]) -> None:
    if not relation_ids:
        return
    now = _now()
    for relation_id in relation_ids:
        ensure_lifecycle(db, relation_id)
        db.execute(
            """
            UPDATE memory_lifecycle
            SET access_count = access_count + 1, last_accessed_at = ?, updated_at = ?
            WHERE relation_id = ?
            """,
            (now, now, relation_id),
        )
    db.commit()


def set_memory_pinned(db: sqlite3.Connection, relation_id: str, pinned: bool) -> bool:
    exists = db.execute(
        "SELECT relation_id FROM memory_relations WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()
    if exists is None:
        return False
    ensure_lifecycle(db, relation_id)
    result = db.execute(
        """
        UPDATE memory_lifecycle
        SET pinned = ?, decay_score = CASE WHEN ? THEN 1.0 ELSE decay_score END,
            updated_at = ?
        WHERE relation_id = ?
        """,
        (int(pinned), int(pinned), _now(), relation_id),
    )
    db.commit()
    return result.rowcount > 0


def _half_life_days(*, predicate: str, provenance: str, active: bool) -> float:
    if not active:
        return 30.0
    if predicate.startswith("has_") or predicate == "prefers":
        return 3650.0
    if predicate in {"works_on", "uses", "depends_on", "supports"}:
        return 240.0
    if provenance == "knowledge_graph":
        return 180.0
    if provenance == "session_summary":
        return 365.0
    return 540.0


def _access_boost(row: sqlite3.Row, now: datetime) -> float:
    count = int(row["access_count"] or 0)
    boost = min(0.25, math.log1p(count) * 0.05)
    last_accessed = str(row["last_accessed_at"] or "")
    if last_accessed and _age_days(last_accessed, now) <= 30:
        boost += 0.1
    return boost


def _age_days(raw: str, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _utcnow().isoformat()
