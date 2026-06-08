from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
import uuid
from typing import Any

from iris.config import IrisConfig
from iris.memory_extract import ExtractedRelation, extract_relations
from iris.memory_lifecycle import (
    ensure_lifecycle,
    lifecycle_by_relation,
    record_memory_access,
)
from iris.memory_vectors import (
    record_retrieval_event,
    semantic_search,
    upsert_text_embedding,
)


@dataclass(frozen=True)
class GraphFact:
    relation_id: str
    subject: str
    predicate: str
    object_value: str
    active: bool
    confidence: float
    provenance: str
    updated_at: str
    evidence_count: int = 0
    source_quote: str = ""

    @property
    def content(self) -> str:
        if self.source_quote:
            return self.source_quote
        return f"{self.subject} {self.predicate.replace('_', ' ')} {self.object_value}"


def record_memory_fact(
    db: sqlite3.Connection,
    *,
    memory_id: str,
    category: str,
    content: str,
    provenance: str,
    confidence: float,
    config: IrisConfig | None = None,
) -> list[str]:
    relations = extract_relations(content, category=category, confidence=confidence)
    if not relations:
        return []
    observation_id = _ensure_observation(
        db,
        content=content,
        source_type="memory",
        source_id=memory_id,
        category=category,
        confidence=confidence,
        metadata={"provenance": provenance},
    )
    if _has_evidence(db, source_table="memories", source_id=memory_id, quote=content):
        return []
    upsert_text_embedding(
        db,
        source_type="memory_observation",
        source_id=observation_id,
        text=content,
        config=config,
        metadata={"category": category, "memory_id": memory_id},
    )
    relation_ids = [
        _upsert_relation(
            db,
            relation=relation,
            observation_id=observation_id,
            source_table="memories",
            source_id=memory_id,
            quote=content,
            provenance=provenance,
            config=config,
        )
        for relation in relations
    ]
    db.commit()
    return relation_ids


def record_extracted_memory_relations(
    db: sqlite3.Connection,
    *,
    memory_id: str,
    category: str,
    content: str,
    provenance: str,
    confidence: float,
    relations: list[ExtractedRelation],
    pending: list[dict[str, Any]] | None = None,
    config: IrisConfig | None = None,
) -> list[str]:
    return record_extracted_relations(
        db,
        source_type="memory",
        source_table="memories",
        source_id=memory_id,
        category=category,
        content=content,
        provenance=provenance,
        confidence=confidence,
        relations=relations,
        pending=pending,
        config=config,
    )


def record_extracted_relations(
    db: sqlite3.Connection,
    *,
    source_type: str,
    source_table: str,
    source_id: str,
    category: str,
    content: str,
    provenance: str,
    confidence: float,
    relations: list[ExtractedRelation],
    pending: list[dict[str, Any]] | None = None,
    config: IrisConfig | None = None,
) -> list[str]:
    observation_id = _ensure_observation(
        db,
        content=content,
        source_type=source_type,
        source_id=source_id,
        category=category,
        confidence=confidence,
        metadata={"provenance": provenance},
    )
    relation_ids = [
        _upsert_relation(
            db,
            relation=relation,
            observation_id=observation_id,
            source_table=source_table,
            source_id=source_id,
            quote=str(relation.metadata.get("evidence") or content),
            provenance=provenance,
            config=config,
        )
        for relation in relations
    ]
    for item in pending or []:
        _add_pending_relation(
            db,
            observation_id=observation_id,
            source_type=source_type,
            source_id=source_id,
            reason=str(item.get("reason") or "pending_confirmation"),
            candidate=item,
        )
    db.commit()
    return relation_ids


def deactivate_relations_for_source(
    db: sqlite3.Connection,
    *,
    source_table: str,
    source_id: str,
    reason: str,
) -> int:
    now = _now()
    rows = db.execute(
        """
        SELECT DISTINCT r.relation_id, r.metadata_json
        FROM memory_relations r
        JOIN memory_evidence e ON e.relation_id = r.relation_id
        WHERE e.source_table = ? AND e.source_id = ? AND r.active = 1
        """,
        (source_table, source_id),
    ).fetchall()
    for row in rows:
        metadata = _loads(row["metadata_json"])
        metadata["deactivated_reason"] = reason
        db.execute(
            """
            UPDATE memory_relations
            SET active = 0, updated_at = ?, metadata_json = ?
            WHERE relation_id = ?
            """,
            (
                now,
                json.dumps(metadata, sort_keys=True),
                row["relation_id"],
            ),
        )
    if rows:
        db.commit()
    return len(rows)


def backfill_memories(db: sqlite3.Connection) -> int:
    count = 0
    rows = db.execute("SELECT * FROM memories ORDER BY created_at ASC").fetchall()
    for row in rows:
        upsert_text_embedding(
            db,
            source_type="memory",
            source_id=str(row["memory_id"]),
            text=str(row["content"]),
            metadata={
                "category": str(row["category"]),
                "provenance": str(row["provenance"]),
            },
        )
        relation_ids = record_memory_fact(
            db,
            memory_id=str(row["memory_id"]),
            category=str(row["category"]),
            content=str(row["content"]),
            provenance=str(row["provenance"]),
            confidence=float(row["confidence"]),
        )
        count += len(relation_ids)
    db.commit()
    return count


def search_facts(
    db: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
    include_inactive: bool = False,
    config: IrisConfig | None = None,
) -> list[dict[str, Any]]:
    terms = _terms(query)
    if not terms:
        return []
    facts = _fetch_facts(db, include_inactive=include_inactive)
    lifecycle = lifecycle_by_relation(db, {fact.relation_id for fact in facts})
    semantic_results = semantic_search(
        db,
        query,
        source_types={"memory_relation"},
        limit=max(limit * 3, 20),
        config=config,
    )
    semantic_scores = {
        str(item["source_id"]): float(item["score"]) for item in semantic_results
    }
    scored: list[tuple[float, GraphFact]] = []
    for fact in facts:
        haystack = " ".join(
            [fact.subject, fact.predicate, fact.object_value, fact.provenance]
        ).lower()
        score = sum(1.0 for term in terms if term in haystack)
        semantic_score = semantic_scores.get(fact.relation_id, 0.0)
        if not score and semantic_score < 0.05:
            continue
        if fact.active:
            score += 2.0
        score += max(0.0, min(fact.confidence, 1.0))
        score += semantic_score * 5.0
        score *= _lifecycle_score(lifecycle.get(fact.relation_id))
        scored.append((score, fact))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    results = [
        _fact_payload(fact, score=score, lifecycle=lifecycle.get(fact.relation_id))
        for score, fact in scored[:limit]
    ]
    record_memory_access(db, [str(item["relation_id"]) for item in results])
    record_retrieval_event(
        db,
        query=query,
        source="memory_graph.search_facts",
        selected=results,
        metadata={"include_inactive": include_inactive},
    )
    return results


def related_facts(
    db: sqlite3.Connection,
    entity_query: str,
    *,
    limit: int = 10,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    normalized = _normalize(entity_query)
    if not normalized:
        return []
    facts = _fetch_facts(db, include_inactive=include_inactive)
    lifecycle = lifecycle_by_relation(db, {fact.relation_id for fact in facts})
    scored: list[tuple[float, GraphFact]] = []
    for fact in facts:
        subject = _normalize(fact.subject)
        obj = _normalize(fact.object_value)
        score = 0.0
        if normalized == subject or normalized == obj:
            score = 5.0
        elif normalized in subject or normalized in obj:
            score = 3.0
        elif subject in normalized or obj in normalized:
            score = 2.0
        if score:
            if fact.active:
                score += 1.0
            score *= _lifecycle_score(lifecycle.get(fact.relation_id))
            scored.append((score, fact))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    results = [
        _fact_payload(fact, score=score, lifecycle=lifecycle.get(fact.relation_id))
        for score, fact in scored[:limit]
    ]
    record_memory_access(db, [str(item["relation_id"]) for item in results])
    return results


def explain_fact(db: sqlite3.Connection, query: str) -> dict[str, Any] | None:
    matches = search_facts(db, query, limit=1, include_inactive=True)
    if not matches:
        matches = related_facts(db, query, limit=1, include_inactive=True)
    if not matches:
        return None
    relation_id = str(matches[0]["relation_id"])
    evidence = [
        dict(row)
        for row in db.execute(
            """
            SELECT e.*, o.category, o.confidence AS observation_confidence
            FROM memory_evidence e
            LEFT JOIN memory_observations o ON o.observation_id = e.observation_id
            WHERE e.relation_id = ?
            ORDER BY e.created_at DESC
            """,
            (relation_id,),
        ).fetchall()
    ]
    return {**matches[0], "evidence": evidence}


def memory_context_packet(
    db: sqlite3.Connection,
    *,
    query: str = "",
    limit: int = 12,
    config: IrisConfig | None = None,
) -> list[dict[str, Any]]:
    results = search_facts(db, query, limit=limit, config=config) if query else []
    seen = {str(item["relation_id"]) for item in results}
    if len(results) < limit:
        fallback_facts = _fetch_facts(db, include_inactive=False)
        lifecycle = lifecycle_by_relation(
            db, {fact.relation_id for fact in fallback_facts}
        )
        fallback_facts.sort(
            key=lambda fact: (
                bool((lifecycle.get(fact.relation_id) or {}).get("pinned")),
                _lifecycle_score(lifecycle.get(fact.relation_id)),
                fact.updated_at,
            ),
            reverse=True,
        )
        for fact in fallback_facts:
            if fact.relation_id in seen:
                continue
            results.append(
                _fact_payload(
                    fact,
                    score=0.0,
                    lifecycle=lifecycle.get(fact.relation_id),
                )
            )
            seen.add(fact.relation_id)
            if len(results) >= limit:
                break
    return results


def format_fact_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "I did not find matching graph memory."
    lines = [f"I found {len(results)} graph memory result(s):"]
    for index, item in enumerate(results[:8], start=1):
        status = "" if item.get("active", True) else " (superseded)"
        lines.append(
            f"{index}. {item['subject']} {str(item['predicate']).replace('_', ' ')} "
            f"{item['object_value']}{status}"
        )
    return "\n".join(lines)


def _upsert_relation(
    db: sqlite3.Connection,
    *,
    relation: ExtractedRelation,
    observation_id: str,
    source_table: str,
    source_id: str,
    quote: str,
    provenance: str,
    config: IrisConfig | None,
) -> str:
    now = _now()
    subject_id = _ensure_entity(
        db, name=relation.subject, kind=relation.subject_kind, metadata={}
    )
    object_id = _ensure_entity(
        db,
        name=relation.object_value,
        kind=relation.object_kind,
        metadata={"source": "memory_relation_object"},
    )
    metadata = dict(relation.metadata)
    if relation.conflict_key:
        metadata["conflict_key"] = relation.conflict_key
        _deactivate_conflicts(
            db,
            subject_id=subject_id,
            predicate=relation.predicate,
            object_value=relation.object_value,
            conflict_key=relation.conflict_key,
            now=now,
        )

    existing = db.execute(
        """
        SELECT relation_id, confidence, metadata_json
        FROM memory_relations
        WHERE subject_entity_id = ?
          AND predicate = ?
          AND object_value = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (subject_id, relation.predicate, relation.object_value),
    ).fetchone()
    if existing:
        relation_id = str(existing["relation_id"])
        db.execute(
            """
            UPDATE memory_relations
            SET object_entity_id = ?, active = 1, confidence = ?, provenance = ?,
                updated_at = ?, metadata_json = ?
            WHERE relation_id = ?
            """,
            (
                object_id,
                max(float(existing["confidence"]), relation.confidence),
                provenance,
                now,
                json.dumps(_merge_metadata(existing["metadata_json"], metadata)),
                relation_id,
            ),
        )
    else:
        relation_id = uuid.uuid4().hex
        db.execute(
            """
            INSERT INTO memory_relations (
              relation_id, subject_entity_id, predicate, object_entity_id,
              object_value, active, confidence, provenance, created_at, updated_at,
              metadata_json
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                subject_id,
                relation.predicate,
                object_id,
                relation.object_value,
                relation.confidence,
                provenance,
                now,
                now,
                json.dumps(metadata, sort_keys=True),
            ),
        )
    ensure_lifecycle(db, relation_id)
    _add_evidence(
        db,
        relation_id=relation_id,
        observation_id=observation_id,
        source_table=source_table,
        source_id=source_id,
        quote=quote,
    )
    upsert_text_embedding(
        db,
        source_type="memory_relation",
        source_id=relation_id,
        text=f"{relation.subject} {relation.predicate.replace('_', ' ')} {relation.object_value}. Evidence: {quote}",
        config=config,
        metadata={
            "subject": relation.subject,
            "predicate": relation.predicate,
            "object_value": relation.object_value,
        },
    )
    return relation_id


def _ensure_entity(
    db: sqlite3.Connection,
    *,
    name: str,
    kind: str,
    metadata: dict[str, object],
) -> str:
    now = _now()
    normalized = _normalize(name)
    row = db.execute(
        "SELECT entity_id FROM memory_entities WHERE normalized_name = ?",
        (normalized,),
    ).fetchone()
    if row:
        entity_id = str(row["entity_id"])
        db.execute(
            "UPDATE memory_entities SET updated_at = ? WHERE entity_id = ?",
            (now, entity_id),
        )
        return entity_id
    entity_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO memory_entities
        (entity_id, name, normalized_name, kind, created_at, updated_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            name.strip(),
            normalized,
            kind,
            now,
            now,
            json.dumps(metadata, sort_keys=True),
        ),
    )
    return entity_id


def _ensure_observation(
    db: sqlite3.Connection,
    *,
    content: str,
    source_type: str,
    source_id: str,
    category: str,
    confidence: float,
    metadata: dict[str, object],
) -> str:
    row = db.execute(
        """
        SELECT observation_id
        FROM memory_observations
        WHERE source_type = ? AND source_id = ? AND content = ?
        """,
        (source_type, source_id, content),
    ).fetchone()
    if row:
        return str(row["observation_id"])
    observation_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO memory_observations
        (observation_id, content, source_type, source_id, category, confidence, created_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            content,
            source_type,
            source_id,
            category,
            confidence,
            _now(),
            json.dumps(metadata, sort_keys=True),
        ),
    )
    return observation_id


def _deactivate_conflicts(
    db: sqlite3.Connection,
    *,
    subject_id: str,
    predicate: str,
    object_value: str,
    conflict_key: str,
    now: str,
) -> None:
    rows = db.execute(
        """
        SELECT relation_id, object_value, metadata_json
        FROM memory_relations
        WHERE subject_entity_id = ? AND predicate = ? AND active = 1
        """,
        (subject_id, predicate),
    ).fetchall()
    for row in rows:
        metadata = _loads(row["metadata_json"])
        if (
            metadata.get("conflict_key") == conflict_key
            and str(row["object_value"]).lower() != object_value.lower()
        ):
            db.execute(
                "UPDATE memory_relations SET active = 0, updated_at = ? WHERE relation_id = ?",
                (now, row["relation_id"]),
            )


def _add_evidence(
    db: sqlite3.Connection,
    *,
    relation_id: str,
    observation_id: str,
    source_table: str,
    source_id: str,
    quote: str,
) -> None:
    exists = db.execute(
        """
        SELECT evidence_id
        FROM memory_evidence
        WHERE relation_id = ? AND source_table = ? AND source_id = ? AND quote = ?
        """,
        (relation_id, source_table, source_id, quote),
    ).fetchone()
    if exists:
        return
    db.execute(
        """
        INSERT INTO memory_evidence
        (evidence_id, relation_id, observation_id, source_table, source_id, quote, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            relation_id,
            observation_id,
            source_table,
            source_id,
            quote,
            _now(),
        ),
    )


def _add_pending_relation(
    db: sqlite3.Connection,
    *,
    observation_id: str,
    source_type: str,
    source_id: str,
    reason: str,
    candidate: dict[str, Any],
) -> None:
    candidate_json = json.dumps(candidate, sort_keys=True, default=str)
    existing = db.execute(
        """
        SELECT pending_id
        FROM memory_pending_relations
        WHERE source_type = ? AND source_id = ? AND candidate_json = ?
        """,
        (source_type, source_id, candidate_json),
    ).fetchone()
    if existing:
        _ensure_review_item(
            db,
            pending_id=str(existing["pending_id"]),
            reason=reason,
            candidate_json=candidate_json,
        )
        return
    pending_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO memory_pending_relations
        (pending_id, observation_id, source_type, source_id, reason, candidate_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pending_id,
            observation_id,
            source_type,
            source_id,
            reason,
            candidate_json,
            _now(),
        ),
    )
    _ensure_review_item(
        db,
        pending_id=pending_id,
        reason=reason,
        candidate_json=candidate_json,
    )


def _ensure_review_item(
    db: sqlite3.Connection,
    *,
    pending_id: str,
    reason: str,
    candidate_json: str,
) -> None:
    exists = db.execute(
        """
        SELECT review_id
        FROM memory_review_items
        WHERE pending_id = ?
        """,
        (pending_id,),
    ).fetchone()
    if exists:
        return
    now = _now()
    db.execute(
        """
        INSERT INTO memory_review_items
        (review_id, pending_id, status, reason, candidate_json, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?, ?, ?)
        """,
        (uuid.uuid4().hex, pending_id, reason, candidate_json, now, now),
    )


def _has_evidence(
    db: sqlite3.Connection,
    *,
    source_table: str,
    source_id: str,
    quote: str,
) -> bool:
    row = db.execute(
        """
        SELECT evidence_id
        FROM memory_evidence
        WHERE source_table = ? AND source_id = ? AND quote = ?
        LIMIT 1
        """,
        (source_table, source_id, quote),
    ).fetchone()
    return row is not None


def _fetch_facts(db: sqlite3.Connection, *, include_inactive: bool) -> list[GraphFact]:
    active_clause = "" if include_inactive else "WHERE r.active = 1"
    rows = db.execute(
        f"""
        SELECT r.*, s.name AS subject_name, COUNT(e.evidence_id) AS evidence_count,
               MAX(e.quote) AS source_quote
        FROM memory_relations r
        JOIN memory_entities s ON s.entity_id = r.subject_entity_id
        LEFT JOIN memory_evidence e ON e.relation_id = r.relation_id
        {active_clause}
        GROUP BY r.relation_id
        ORDER BY r.active DESC, r.updated_at DESC
        LIMIT 500
        """
    ).fetchall()
    return [
        GraphFact(
            relation_id=str(row["relation_id"]),
            subject=str(row["subject_name"]),
            predicate=str(row["predicate"]),
            object_value=str(row["object_value"]),
            active=bool(row["active"]),
            confidence=float(row["confidence"]),
            provenance=str(row["provenance"]),
            updated_at=str(row["updated_at"]),
            evidence_count=int(row["evidence_count"]),
            source_quote=str(row["source_quote"] or ""),
        )
        for row in rows
    ]


def _fact_payload(
    fact: GraphFact, *, score: float, lifecycle: dict[str, Any] | None = None
) -> dict[str, Any]:
    lifecycle = lifecycle or {}
    return {
        "relation_id": fact.relation_id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "object_value": fact.object_value,
        "content": fact.content,
        "relation_content": (
            f"{fact.subject} {fact.predicate.replace('_', ' ')} {fact.object_value}"
        ),
        "active": fact.active,
        "confidence": fact.confidence,
        "provenance": fact.provenance,
        "updated_at": fact.updated_at,
        "evidence_count": fact.evidence_count,
        "decay_score": float(lifecycle.get("decay_score") or 1.0),
        "pinned": bool(lifecycle.get("pinned")),
        "review_status": str(lifecycle.get("review_status") or "active"),
        "last_accessed_at": lifecycle.get("last_accessed_at"),
        "access_count": int(lifecycle.get("access_count") or 0),
        "score": score,
    }


def _lifecycle_score(lifecycle: dict[str, Any] | None) -> float:
    if not lifecycle:
        return 1.0
    if bool(lifecycle.get("pinned")):
        return 1.0
    if str(lifecycle.get("review_status") or "") == "stale":
        return min(float(lifecycle.get("decay_score") or 0.2), 0.2)
    return max(0.03, min(float(lifecycle.get("decay_score") or 1.0), 1.0))


def _terms(query: str) -> list[str]:
    return [term for term in re.split(r"\W+", query.lower()) if len(term) >= 2]


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _merge_metadata(raw: object, update: dict[str, object]) -> dict[str, object]:
    metadata = _loads(raw)
    metadata.update(update)
    return metadata


def _loads(raw: object) -> dict[str, object]:
    try:
        data = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
