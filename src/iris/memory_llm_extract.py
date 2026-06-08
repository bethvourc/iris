from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from iris.config import IrisConfig
from iris.memory_extract import ExtractedRelation
from iris.memory_graph import (
    deactivate_relations_for_source,
    record_extracted_memory_relations,
)


MIN_ACTIVE_CONFIDENCE = 0.6
MIN_PENDING_CONFIDENCE = 0.45


@dataclass(frozen=True)
class RichExtractionResult:
    active_relations: list[ExtractedRelation]
    pending_candidates: list[dict[str, Any]]
    rejected_count: int = 0


def enrich_memory_with_llm(
    db,
    *,
    memory_id: str,
    category: str,
    content: str,
    provenance: str,
    confidence: float,
    openai_client: Any,
    config: IrisConfig,
) -> RichExtractionResult:
    result = extract_rich_relations(
        content, category=category, openai_client=openai_client
    )
    if not result.active_relations and not result.pending_candidates:
        return result
    try:
        if result.pending_candidates:
            deactivate_relations_for_source(
                db,
                source_table="memories",
                source_id=memory_id,
                reason="pending_llm_confirmation",
            )
        record_extracted_memory_relations(
            db,
            memory_id=memory_id,
            category=category,
            content=content,
            provenance=provenance,
            confidence=confidence,
            relations=result.active_relations,
            pending=result.pending_candidates,
            config=config,
        )
    except Exception:
        # Rich extraction is optional; flat memory must remain reliable.
        return result
    return result


def extract_rich_relations(
    content: str,
    *,
    category: str,
    openai_client: Any,
) -> RichExtractionResult:
    if not content.strip() or not getattr(openai_client, "available", False):
        return RichExtractionResult([], [])
    try:
        client = openai_client._get_client()
        response = client.responses.create(
            model=openai_client.config.chat_model,
            instructions=_INSTRUCTIONS,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {"category": category, "memory": content},
                                sort_keys=True,
                            ),
                        }
                    ],
                }
            ],
            max_output_tokens=900,
        )
        raw = openai_client.output_text(response)
    except Exception:
        return RichExtractionResult([], [])
    return parse_rich_extraction(raw, source_text=content)


def parse_rich_extraction(raw: str, *, source_text: str) -> RichExtractionResult:
    try:
        payload = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return RichExtractionResult([], [], rejected_count=1)
    items = payload.get("relations") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return RichExtractionResult([], [], rejected_count=1)

    active: list[ExtractedRelation] = []
    pending: list[dict[str, Any]] = []
    rejected = 0
    for item in items[:12]:
        candidate = _candidate(item, source_text=source_text)
        if candidate is None:
            rejected += 1
            continue
        if candidate["confidence"] < MIN_PENDING_CONFIDENCE:
            rejected += 1
            continue
        if candidate["sensitive"] or candidate["ambiguous"]:
            reason = "sensitive" if candidate["sensitive"] else "ambiguous"
            pending.append({**candidate, "reason": reason})
            continue
        if candidate["confidence"] < MIN_ACTIVE_CONFIDENCE:
            pending.append({**candidate, "reason": "low_confidence"})
            continue
        active.append(
            ExtractedRelation(
                subject=str(candidate["subject"]),
                subject_kind=str(candidate["subject_kind"]),
                predicate=str(candidate["predicate"]),
                object_value=str(candidate["object_value"]),
                object_kind=str(candidate["object_kind"]),
                confidence=float(candidate["confidence"]),
                conflict_key=candidate.get("conflict_key")
                if isinstance(candidate.get("conflict_key"), str)
                else None,
                metadata={
                    "extraction": "llm",
                    "sensitive": False,
                    "ambiguous": False,
                    "evidence": candidate["evidence"],
                },
            )
        )
    return RichExtractionResult(active, pending, rejected)


def _candidate(raw: Any, *, source_text: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    subject = _clean(raw.get("subject") or "")
    predicate = _slug(raw.get("predicate") or "")
    object_value = _clean(raw.get("object") or raw.get("object_value") or "")
    if not subject or not predicate or not object_value:
        return None
    confidence = _float(raw.get("confidence"), 0.0)
    evidence = _clean(raw.get("evidence") or "")
    if evidence and evidence.lower() not in source_text.lower():
        evidence = _best_evidence(source_text, object_value)
    if not evidence:
        evidence = _best_evidence(source_text, object_value)
    return {
        "subject": subject[:120],
        "subject_kind": _kind(raw.get("subject_kind"), "concept"),
        "predicate": predicate[:80],
        "object_value": object_value[:300],
        "object_kind": _kind(raw.get("object_kind"), "concept"),
        "confidence": max(0.0, min(confidence, 1.0)),
        "sensitive": bool(raw.get("sensitive")),
        "ambiguous": bool(raw.get("ambiguous")),
        "conflict_key": _clean(raw.get("conflict_key") or "") or None,
        "evidence": evidence[:500],
    }


def _best_evidence(source_text: str, object_value: str) -> str:
    cleaned = _clean(source_text)
    if object_value.lower() in cleaned.lower():
        return cleaned[:500]
    return cleaned[:300]


def _kind(raw: Any, default: str) -> str:
    value = _slug(raw or "")
    return value or default


def _float(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _clean(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw)).strip()


def _slug(raw: Any) -> str:
    value = re.sub(r"[^a-zA-Z0-9_ -]+", "", str(raw)).strip().lower()
    value = re.sub(r"[\s-]+", "_", value)
    return value.strip("_")


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


_INSTRUCTIONS = """
Extract durable graph memory from the provided Iris memory text.

Return strict JSON only:
{
  "relations": [
    {
      "subject": "user | named entity",
      "subject_kind": "person|project|app|organization|concept|preference|fact",
      "predicate": "short snake_case relation",
      "object": "stable object value",
      "object_kind": "person|project|app|organization|concept|preference|fact",
      "confidence": 0.0-1.0,
      "sensitive": true|false,
      "ambiguous": true|false,
      "conflict_key": "optional stable key for mutually exclusive facts",
      "evidence": "short exact phrase from the input"
    }
  ]
}

Rules:
- Extract only stable facts, preferences, standing instructions, project facts, or relationships.
- Do not extract one-off tasks, chit-chat, guesses, or private/sensitive inferences unless explicitly stated.
- Mark health, finances, credentials, precise location, personal identifiers, family/relationship details, and private contact details as sensitive.
- Mark unclear subjects, uncertain wording, or weakly implied facts as ambiguous.
- Use "user" for facts about the Iris owner.
"""
