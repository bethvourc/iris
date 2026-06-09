from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
import uuid
from typing import Any

from iris.memory_extract import ExtractedRelation
from iris.memory_graph import (
    deactivate_relations_for_source,
    record_extracted_relations,
)


@dataclass(frozen=True)
class ReviewDecisionResult:
    ok: bool
    review_id: str
    decision: str
    relation_id: str | None = None
    message: str = ""


def list_review_items(
    db: sqlite3.Connection, *, status: str = "pending", limit: int = 50
) -> list[dict[str, Any]]:
    _backfill_review_items(db)
    rows = db.execute(
        """
        SELECT ri.*, pr.source_type, pr.source_id, pr.observation_id
        FROM memory_review_items ri
        LEFT JOIN memory_pending_relations pr ON pr.pending_id = ri.pending_id
        WHERE ri.status = ?
        ORDER BY ri.created_at ASC
        LIMIT ?
        """,
        (status, max(1, min(limit, 200))),
    ).fetchall()
    return [_review_payload(row) for row in rows]


def decide_review_item(
    db: sqlite3.Connection,
    *,
    review_id: str,
    decision: str,
    actor: str = "user",
    notes: str = "",
) -> ReviewDecisionResult:
    decision = decision.strip().lower()
    if decision not in {"approve", "reject", "supersede"}:
        return ReviewDecisionResult(
            False, review_id, decision, message="Invalid decision."
        )
    row = db.execute(
        """
        SELECT ri.*, pr.source_type, pr.source_id
        FROM memory_review_items ri
        LEFT JOIN memory_pending_relations pr ON pr.pending_id = ri.pending_id
        WHERE ri.review_id = ?
        """,
        (review_id,),
    ).fetchone()
    if row is None:
        return ReviewDecisionResult(
            False, review_id, decision, message="Review item not found."
        )
    if str(row["status"]) != "pending":
        return ReviewDecisionResult(
            False,
            review_id,
            decision,
            message=f"Review item is already {row['status']}.",
        )

    relation_id: str | None = None
    if decision in {"approve", "supersede"}:
        source_type = str(row["source_type"] or "")
        source_id = str(row["source_id"] or "")
        if not source_type or not source_id:
            return ReviewDecisionResult(
                False, review_id, decision, message="Missing source."
            )
        candidate = _loads_candidate(row["candidate_json"])
        relation = _candidate_relation(candidate)
        if relation is None:
            return ReviewDecisionResult(
                False,
                review_id,
                decision,
                message="Candidate cannot be converted into a relation.",
            )
        if decision == "supersede":
            deactivate_relations_for_source(
                db,
                source_table=_source_table(source_type),
                source_id=source_id,
                reason="review_superseded",
            )
        relation_ids = record_extracted_relations(
            db,
            source_type=source_type,
            source_table=_source_table(source_type),
            source_id=source_id,
            category="review",
            content=str(
                candidate.get("evidence") or candidate.get("object_value") or ""
            ),
            provenance="memory_review",
            confidence=float(candidate.get("confidence") or 0.7),
            relations=[relation],
        )
        relation_id = relation_ids[0] if relation_ids else None

    status = {
        "approve": "approved",
        "reject": "rejected",
        "supersede": "superseded",
    }[decision]
    now = _now()
    db.execute(
        """
        UPDATE memory_review_items
        SET status = ?, updated_at = ?
        WHERE review_id = ?
        """,
        (status, now, review_id),
    )
    db.execute(
        """
        INSERT INTO memory_review_decisions
        (decision_id, review_id, decision, actor, relation_id, created_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (uuid.uuid4().hex, review_id, decision, actor, relation_id, now, notes),
    )
    db.commit()
    return ReviewDecisionResult(
        True,
        review_id,
        decision,
        relation_id=relation_id,
        message=f"Review item {status}.",
    )


def _backfill_review_items(db: sqlite3.Connection) -> None:
    rows = db.execute(
        """
        SELECT pr.*
        FROM memory_pending_relations pr
        LEFT JOIN memory_review_items ri ON ri.pending_id = pr.pending_id
        WHERE ri.review_id IS NULL
        """
    ).fetchall()
    now = _now()
    for row in rows:
        db.execute(
            """
            INSERT INTO memory_review_items
            (review_id, pending_id, status, reason, candidate_json, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                row["pending_id"],
                row["reason"],
                row["candidate_json"],
                now,
                now,
            ),
        )
    if rows:
        db.commit()


def _review_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "review_id": row["review_id"],
        "pending_id": row["pending_id"],
        "status": row["status"],
        "reason": row["reason"],
        "candidate": _loads_candidate(row["candidate_json"]),
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _candidate_relation(candidate: dict[str, Any]) -> ExtractedRelation | None:
    subject = str(candidate.get("subject") or "").strip()
    predicate = str(candidate.get("predicate") or "").strip()
    object_value = str(
        candidate.get("object_value") or candidate.get("object") or ""
    ).strip()
    if not subject or not predicate or not object_value:
        return None
    return ExtractedRelation(
        subject=subject,
        subject_kind=str(candidate.get("subject_kind") or "concept"),
        predicate=predicate,
        object_value=object_value,
        object_kind=str(candidate.get("object_kind") or "concept"),
        confidence=float(candidate.get("confidence") or 0.7),
        conflict_key=candidate.get("conflict_key")
        if isinstance(candidate.get("conflict_key"), str)
        else None,
        metadata={
            "extraction": "review",
            "evidence": str(candidate.get("evidence") or object_value),
        },
    )


def _source_table(source_type: str) -> str:
    return {
        "memory": "memories",
        "knowledge_chunk": "knowledge_chunks",
    }.get(source_type, source_type)


def _loads_candidate(raw: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
