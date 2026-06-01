from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".tsv",
    ".log",
    ".rst",
    ".text",
}
MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class IngestResult:
    scanned: int
    ingested: int
    skipped: int
    linked: int
    errors: list[str]


def ingest_folder(
    db: sqlite3.Connection,
    folder: Path,
    *,
    limit: int = 200,
    recursive: bool = True,
) -> IngestResult:
    root = folder.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Folder not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a folder: {root}")

    scanned = 0
    ingested = 0
    skipped = 0
    errors: list[str] = []
    candidates = root.rglob("*") if recursive else root.iterdir()
    for path in candidates:
        if ingested >= limit:
            break
        if not path.is_file() or path.name.startswith("."):
            continue
        scanned += 1
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            skipped += 1
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped += 1
                continue
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                skipped += 1
                continue
            upsert_file_page(db, path, content)
            ingested += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"{path}: {exc}")

    linked = rebuild_links(db)
    db.commit()
    return IngestResult(
        scanned=scanned,
        ingested=ingested,
        skipped=skipped,
        linked=linked,
        errors=errors[:20],
    )


def upsert_file_page(db: sqlite3.Connection, path: Path, content: str) -> str:
    now = _now()
    resolved = path.expanduser().resolve()
    uri = resolved.as_uri()
    title = _title_from_path(resolved, content)
    source_row = db.execute(
        "SELECT source_id FROM knowledge_sources WHERE uri = ?",
        (uri,),
    ).fetchone()
    source_id = str(source_row["source_id"]) if source_row else uuid.uuid4().hex
    metadata = {
        "path": str(resolved),
        "extension": resolved.suffix.lower(),
        "size_bytes": resolved.stat().st_size,
    }
    if source_row:
        db.execute(
            """
            UPDATE knowledge_sources
            SET kind = ?, title = ?, updated_at = ?, metadata_json = ?
            WHERE source_id = ?
            """,
            ("file", title, now, json.dumps(metadata, sort_keys=True), source_id),
        )
    else:
        db.execute(
            """
            INSERT INTO knowledge_sources
            (source_id, kind, uri, title, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                "file",
                uri,
                title,
                now,
                now,
                json.dumps(metadata, sort_keys=True),
            ),
        )

    page_row = db.execute(
        "SELECT page_id FROM knowledge_pages WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    page_id = str(page_row["page_id"]) if page_row else uuid.uuid4().hex
    summary = _summarize(content)
    if page_row:
        db.execute(
            """
            UPDATE knowledge_pages
            SET title = ?, content = ?, summary = ?, updated_at = ?, metadata_json = ?
            WHERE page_id = ?
            """,
            (
                title,
                content,
                summary,
                now,
                json.dumps(metadata, sort_keys=True),
                page_id,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO knowledge_pages
            (page_id, source_id, title, content, summary, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                source_id,
                title,
                content,
                summary,
                now,
                now,
                json.dumps(metadata, sort_keys=True),
            ),
        )
    return page_id


def rebuild_links(db: sqlite3.Connection) -> int:
    now = _now()
    pages = [
        dict(row)
        for row in db.execute(
            "SELECT page_id, title, content FROM knowledge_pages ORDER BY updated_at DESC"
        ).fetchall()
    ]
    db.execute("DELETE FROM knowledge_links")
    count = 0
    for source in pages:
        content = str(source["content"]).lower()
        from_id = str(source["page_id"])
        for target in pages:
            to_id = str(target["page_id"])
            title = str(target["title"]).strip()
            if from_id == to_id or len(title) < 3:
                continue
            if re.search(rf"\b{re.escape(title.lower())}\b", content):
                db.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_links
                    (from_page_id, to_page_id, label, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (from_id, to_id, title, now),
                )
                count += 1
    return count


def search_pages(
    db: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    cleaned = _clean_space(query).lower()
    if not cleaned:
        return []
    terms = [term for term in re.split(r"\W+", cleaned) if len(term) >= 2]
    if not terms:
        terms = [cleaned]
    rows = [
        dict(row)
        for row in db.execute(
            """
            SELECT p.*, s.uri, s.kind AS source_kind
            FROM knowledge_pages p
            LEFT JOIN knowledge_sources s ON s.source_id = p.source_id
            ORDER BY p.updated_at DESC
            LIMIT 1000
            """
        ).fetchall()
    ]
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        title = str(row.get("title") or "").lower()
        content = str(row.get("content") or "").lower()
        score = 0
        for term in terms:
            if term in title:
                score += 20
            if term in content:
                score += min(10, content.count(term))
        if score:
            row["snippet"] = _snippet(str(row.get("content") or ""), terms)
            row["score"] = score
            scored.append((score, row))
    scored.sort(
        key=lambda item: (item[0], str(item[1].get("updated_at") or "")), reverse=True
    )
    return [row for _, row in scored[: max(1, min(limit, 50))]]


def list_pages(db: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            """
            SELECT p.page_id, p.title, p.summary, p.updated_at, s.uri, s.kind AS source_kind
            FROM knowledge_pages p
            LEFT JOIN knowledge_sources s ON s.source_id = p.source_id
            ORDER BY p.updated_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
    ]


def get_page(db: sqlite3.Connection, page_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT p.*, s.uri, s.kind AS source_kind
        FROM knowledge_pages p
        LEFT JOIN knowledge_sources s ON s.source_id = p.source_id
        WHERE p.page_id = ?
        """,
        (page_id,),
    ).fetchone()
    if row is None:
        return None
    page = dict(row)
    page["outlinks"] = [
        dict(item)
        for item in db.execute(
            """
            SELECT l.label, p.page_id, p.title
            FROM knowledge_links l
            JOIN knowledge_pages p ON p.page_id = l.to_page_id
            WHERE l.from_page_id = ?
            ORDER BY p.title
            """,
            (page_id,),
        ).fetchall()
    ]
    page["backlinks"] = [
        dict(item)
        for item in db.execute(
            """
            SELECT l.label, p.page_id, p.title
            FROM knowledge_links l
            JOIN knowledge_pages p ON p.page_id = l.from_page_id
            WHERE l.to_page_id = ?
            ORDER BY p.title
            """,
            (page_id,),
        ).fetchall()
    ]
    return page


def format_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "I did not find that in the local Iris knowledge base."
    lines = [f"I found {len(results)} local knowledge result(s):"]
    for index, item in enumerate(results[:8], start=1):
        title = item.get("title") or "Untitled"
        snippet = item.get("snippet") or item.get("summary") or ""
        lines.append(f"{index}. {title}: {_clean_space(str(snippet))}")
    return "\n".join(lines)


def _title_from_path(path: Path, content: str) -> str:
    if path.suffix.lower() in {".md", ".markdown", ".rst"}:
        for line in content.splitlines()[:20]:
            cleaned = line.strip().lstrip("#").strip()
            if cleaned:
                return cleaned[:120]
    return path.stem.replace("_", " ").replace("-", " ").strip()[:120] or path.name


def _summarize(content: str) -> str:
    cleaned = _clean_space(content)
    return cleaned[:350].rstrip()


def _snippet(content: str, terms: list[str], *, radius: int = 160) -> str:
    lowered = content.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    if not positions:
        return _summarize(content)
    position = min(positions)
    start = max(0, position - radius)
    end = min(len(content), position + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(content) else ""
    return prefix + _clean_space(content[start:end]) + suffix


def _clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
