from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class ExtractedRelation:
    subject: str
    subject_kind: str
    predicate: str
    object_value: str
    object_kind: str = "concept"
    confidence: float = 0.7
    conflict_key: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


_DURABLE_CATEGORIES = {
    "preferences",
    "preference",
    "facts",
    "fact",
    "contacts",
    "contact",
    "profile",
    "project",
    "session_summary",
}


def extract_relations(
    content: str,
    *,
    category: str = "facts",
    confidence: float = 0.7,
) -> list[ExtractedRelation]:
    """Conservative deterministic extraction for durable local memory.

    The goal is not perfect NLP. It gives Iris a dependable offline path for
    explicit preferences and stable facts, while richer LLM extraction can be
    layered on later without changing storage.
    """
    text = _clean(content)
    if not text:
        return []
    normalized_category = category.strip().lower()
    if normalized_category.startswith("machine_"):
        return []
    if normalized_category not in _DURABLE_CATEGORIES and not _has_durable_signal(text):
        return []

    lowered = text.lower()
    if normalized_category in {"preferences", "preference"} or _has_preference_signal(
        lowered
    ):
        return [_preference_relation(text, confidence)]

    fact = _fact_relation(text, normalized_category, confidence)
    if fact is not None:
        return [fact]
    return [
        ExtractedRelation(
            subject="user",
            subject_kind="person",
            predicate="knows_fact",
            object_value=text,
            object_kind="fact",
            confidence=min(confidence, 0.65),
            metadata={"category": normalized_category},
        )
    ]


def _preference_relation(text: str, confidence: float) -> ExtractedRelation:
    object_value = _preferred_object(text)
    return ExtractedRelation(
        subject="user",
        subject_kind="person",
        predicate="prefers",
        object_value=object_value,
        object_kind="preference",
        confidence=confidence,
        conflict_key=_preference_conflict_key(text),
        metadata={"category": "preferences", "raw": text},
    )


def _fact_relation(
    text: str, category: str, confidence: float
) -> ExtractedRelation | None:
    lowered = text.lower()
    if match := re.search(r"\bmy ([a-z][a-z0-9 _-]{1,40}) is ([^.]+)", lowered):
        field = _slug(match.group(1))
        value = text[match.start(2) :].strip(" .")
        return ExtractedRelation(
            subject="user",
            subject_kind="person",
            predicate=f"has_{field}",
            object_value=value,
            object_kind="fact",
            confidence=confidence,
            conflict_key=f"user_fact:{field}",
            metadata={"category": category},
        )
    if match := re.search(r"\b(?:i am|i'm) working on ([^.]+)", lowered):
        value = text[match.start(1) :].strip(" .")
        return ExtractedRelation(
            subject="user",
            subject_kind="person",
            predicate="works_on",
            object_value=value,
            object_kind="project",
            confidence=confidence,
            metadata={"category": category},
        )
    if match := re.search(r"\b(?:call me|my name is) ([^.]+)", lowered):
        value = text[match.start(1) :].strip(" .")
        return ExtractedRelation(
            subject="user",
            subject_kind="person",
            predicate="has_preferred_name",
            object_value=value,
            object_kind="person",
            confidence=confidence,
            conflict_key="user_fact:preferred_name",
            metadata={"category": category},
        )
    return None


def _preferred_object(text: str) -> str:
    patterns = [
        r"\bprefer(?:s|red)? (.+?)(?: over .+)?$",
        r"\buse (?:the )?(.+?)(?:,? not .+)?$",
        r"\balways (.+)$",
        r"\bfrom now on,? (.+)$",
    ]
    lowered = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            start, end = match.span(1)
            return text[start:end].strip(" .")
    return text


def _preference_conflict_key(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(use|prefer) (?:the )?.*\bapp\b", lowered):
        return "preference:default_app"
    if "browser" in lowered:
        return "preference:browser_usage"
    if "voice" in lowered:
        return "preference:voice"
    if "email" in lowered or "gmail" in lowered:
        return "preference:email"
    return None


def _has_durable_signal(text: str) -> bool:
    lowered = text.lower()
    return (
        _has_preference_signal(lowered)
        or "remember that" in lowered
        or "my " in lowered
        or "call me" in lowered
        or "from now on" in lowered
    )


def _has_preference_signal(lowered: str) -> bool:
    return any(
        signal in lowered
        for signal in ("i prefer", "prefer ", "always ", "from now on", "use the ")
    )


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return cleaned.strip("_") or "fact"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
