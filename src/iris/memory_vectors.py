from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import sqlite3
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError
import uuid

from iris.config import IrisConfig


LOCAL_EMBEDDING_MODEL = "iris-local-hash-v1"
LOCAL_PROVIDER = "local"
VECTOR_DIMENSIONS = 256


def upsert_text_embedding(
    db: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    text: str,
    config: IrisConfig | None = None,
    metadata: dict[str, object] | None = None,
) -> str | None:
    cleaned = _clean(text)
    if not cleaned:
        return None
    if config is not None and not config.memory_embeddings_enabled:
        return None
    provider, model, vector = _embed_one(cleaned, config)
    return _upsert_vector(
        db,
        source_type=source_type,
        source_id=source_id,
        text=cleaned,
        vector=vector,
        provider=provider,
        model=model,
        metadata=metadata or {},
    )


def semantic_search(
    db: sqlite3.Connection,
    query: str,
    *,
    source_types: set[str] | None = None,
    limit: int = 20,
    config: IrisConfig | None = None,
) -> list[dict[str, Any]]:
    cleaned = _clean(query)
    if not cleaned:
        return []
    if config is not None and not config.memory_embeddings_enabled:
        return []
    query_provider, query_model, query_vector = _embed_one(cleaned, config)
    rows = db.execute(
        """
        SELECT *
        FROM memory_embeddings
        ORDER BY updated_at DESC
        LIMIT 2000
        """
    ).fetchall()
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if source_types is not None and str(row["source_type"]) not in source_types:
            continue
        if str(row["provider"]) != query_provider or str(row["model"]) != query_model:
            continue
        vector = _loads_vector(row["vector_json"])
        score = _cosine(query_vector, vector)
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "embedding_id": row["embedding_id"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "text": row["text"],
                    "score": score,
                    "provider": row["provider"],
                    "model": row["model"],
                },
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored[: max(1, min(limit, 100))]]


def record_retrieval_event(
    db: sqlite3.Connection,
    *,
    query: str,
    source: str,
    selected: list[dict[str, Any]],
    metadata: dict[str, object] | None = None,
) -> str:
    event_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO memory_retrieval_events
        (event_id, query, source, selected_json, created_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            query,
            source,
            json.dumps(selected[:25], sort_keys=True, default=str),
            _now(),
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    db.commit()
    return event_id


def local_embedding(text: str) -> list[float]:
    vector = [0.0] * VECTOR_DIMENSIONS
    tokens = _tokens(text)
    for token in tokens:
        _add_feature(vector, token, 1.0)
        if len(token) >= 4:
            for index in range(max(1, len(token) - 2)):
                _add_feature(vector, token[index : index + 3], 0.25)
    magnitude = math.sqrt(sum(value * value for value in vector))
    if not magnitude:
        return vector
    return [value / magnitude for value in vector]


def _embed_one(text: str, config: IrisConfig | None) -> tuple[str, str, list[float]]:
    provider = (
        config.memory_embedding_provider.strip().lower()
        if config is not None
        else LOCAL_PROVIDER
    )
    if provider == "off":
        return LOCAL_PROVIDER, LOCAL_EMBEDDING_MODEL, local_embedding(text)
    if provider in {"openai", "auto"} and config is not None and config.openai_api_key:
        try:
            return (
                "openai",
                config.embedding_model,
                _openai_embedding(
                    text,
                    api_key=config.openai_api_key,
                    model=config.embedding_model,
                ),
            )
        except Exception:
            if provider == "openai":
                raise
    return LOCAL_PROVIDER, LOCAL_EMBEDDING_MODEL, local_embedding(text)


def _openai_embedding(text: str, *, api_key: str, model: str) -> list[float]:
    body = json.dumps({"model": model, "input": text}).encode("utf-8")
    req = request.Request(
        "https://api.openai.com/v1/embeddings",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI Embeddings API failed: {exc.code} {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI Embeddings API connection failed: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise RuntimeError("OpenAI Embeddings API returned no embedding data")
    embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
    if not isinstance(embedding, list):
        raise RuntimeError("OpenAI Embeddings API returned an invalid embedding")
    return [float(value) for value in embedding]


def _upsert_vector(
    db: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    text: str,
    vector: list[float],
    provider: str,
    model: str,
    metadata: dict[str, object],
) -> str:
    now = _now()
    text_hash = _hash(text)
    row = db.execute(
        """
        SELECT embedding_id, text_hash
        FROM memory_embeddings
        WHERE source_type = ? AND source_id = ? AND provider = ? AND model = ?
        """,
        (source_type, source_id, provider, model),
    ).fetchone()
    if row:
        embedding_id = str(row["embedding_id"])
        if str(row["text_hash"]) != text_hash:
            db.execute(
                """
                UPDATE memory_embeddings
                SET text_hash = ?, text = ?, vector_json = ?, dimensions = ?,
                    updated_at = ?, metadata_json = ?
                WHERE embedding_id = ?
                """,
                (
                    text_hash,
                    text,
                    json.dumps(vector),
                    len(vector),
                    now,
                    json.dumps(metadata, sort_keys=True),
                    embedding_id,
                ),
            )
        return embedding_id
    embedding_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO memory_embeddings
        (embedding_id, source_type, source_id, text_hash, text, vector_json, provider,
         model, dimensions, created_at, updated_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            embedding_id,
            source_type,
            source_id,
            text_hash,
            text,
            json.dumps(vector),
            provider,
            model,
            len(vector),
            now,
            now,
            json.dumps(metadata, sort_keys=True),
        ),
    )
    return embedding_id


def _add_feature(vector: list[float], feature: str, weight: float) -> None:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(vector)
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    vector[index] += sign * weight


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"\W+", text.lower()) if len(token) >= 2]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _loads_vector(raw: object) -> list[float]:
    try:
        data = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [float(value) for value in data]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
