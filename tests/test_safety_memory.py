from __future__ import annotations

from pathlib import Path

from iris.actions import LocalAction, RiskLevel
from iris.config import IrisConfig
from iris.knowledge import graph_page, rebuild_links, search_pages, upsert_file_page
from iris.memory import add_memory
from iris.memory_graph import related_facts, search_facts
from iris.memory_llm_extract import parse_rich_extraction
from iris.memory_vectors import semantic_search
from iris.safety import classify_action
from iris.state import open_state
from iris.tools import (
    ToolContext,
    _memory_explain,
    _memory_related,
    _memory_search,
    _recall,
    _remember,
)


def test_readonly_args_do_not_trip_approval() -> None:
    decision = classify_action(
        LocalAction("find_file", {"query": "Spotify Installer", "max_results": 20})
    )
    assert decision.risk == RiskLevel.LOW_RISK

    for query in ("draft a message", "open my login page", "send folder"):
        assert (
            classify_action(LocalAction("file_find", {"query": query})).risk
            == RiskLevel.LOW_RISK
        )


def test_sensitive_and_blocked_actions_still_gated() -> None:
    assert classify_action(LocalAction("send_message", {})).risk == RiskLevel.SENSITIVE
    assert classify_action(LocalAction("delete_file", {})).risk == RiskLevel.BLOCKED
    assert (
        classify_action(LocalAction("run_shell", {"cmd": "rm -rf /"})).risk
        == RiskLevel.BLOCKED
    )
    assert (
        classify_action(LocalAction("run_shell", {"cmd": "ls"})).risk
        == RiskLevel.SENSITIVE
    )


def test_explicit_low_risk_overrides_name() -> None:
    decision = classify_action(
        LocalAction("anything", {"text": "install"}, risk=RiskLevel.LOW_RISK)
    )
    assert decision.risk == RiskLevel.LOW_RISK


def test_remember_and_recall_round_trip(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with open_state(config) as db:
        from iris.state import migrate

        migrate(db)
    context = _memory_context(config)

    saved = _remember({"content": "Use the Spotify app, not the browser"}, context)
    assert saved.ok
    assert saved.payload["category"] == "preferences"

    again = _remember(
        {"content": "use the spotify app, not the browser", "category": "preferences"},
        context,
    )
    assert again.payload.get("duplicate") is True

    recalled = _recall({}, context)
    assert recalled.ok
    contents = [item["content"] for item in recalled.payload["memories"]]
    assert "Use the Spotify app, not the browser" in contents


def test_remember_writes_graph_memory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    context = _memory_context(config)

    saved = _remember({"content": "Use the Spotify app, not the browser"}, context)
    assert saved.ok

    with open_state(config) as db:
        results = search_facts(db, "spotify", limit=5)

    assert results
    assert results[0]["predicate"] == "prefers"
    assert "Spotify app" in results[0]["object_value"]


def test_newer_conflicting_preference_supersedes_older_fact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    context = _memory_context(config)

    first = _remember({"content": "Use the Spotify app, not the browser"}, context)
    second = _remember({"content": "Use the Apple Music app, not the browser"}, context)
    assert first.ok
    assert second.ok

    with open_state(config) as db:
        active = search_facts(db, "app", limit=10)
        all_results = search_facts(db, "app", limit=10, include_inactive=True)

    assert any("Apple Music app" in item["object_value"] for item in active)
    assert not any("Spotify app" in item["object_value"] for item in active)
    spotify = [item for item in all_results if "Spotify app" in item["object_value"]]
    assert spotify and spotify[0]["active"] is False


def test_graph_memory_tools_return_search_related_and_explain(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    context = _memory_context(config)
    _remember({"content": "Use the Spotify app, not the browser"}, context)

    searched = _memory_search({"query": "spotify"}, context)
    related = _memory_related({"entity": "Spotify app"}, context)
    explained = _memory_explain({"query": "spotify"}, context)

    assert searched.ok
    assert searched.payload["results"]
    assert related.ok
    assert related.payload["results"]
    assert explained.ok
    assert explained.payload["evidence"]


def test_existing_flat_memories_backfill_into_graph(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with open_state(config) as db:
        memory_id = add_memory(
            db,
            category="preferences",
            content="Use the Spotify app, not the browser",
        )

    with open_state(config) as db:
        results = related_facts(db, "Spotify app")

    assert memory_id
    assert results


def test_memory_embeddings_and_retrieval_events_are_recorded(tmp_path: Path) -> None:
    config = _config(tmp_path)
    context = _memory_context(config)
    _remember({"content": "Use the Spotify app, not the browser"}, context)

    with open_state(config) as db:
        embeddings = [
            dict(row)
            for row in db.execute(
                "SELECT source_type, source_id FROM memory_embeddings"
            ).fetchall()
        ]
        results = search_facts(db, "spotify", config=config)
        events = db.execute("SELECT * FROM memory_retrieval_events").fetchall()

    source_types = {item["source_type"] for item in embeddings}
    assert {"memory", "memory_observation", "memory_relation"} <= source_types
    assert results
    assert events


def test_semantic_search_uses_local_vector_fallback(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with open_state(config) as db:
        memory_id = add_memory(
            db,
            category="preferences",
            content="Use Apple Music app for playback",
        )
        results = semantic_search(db, "music playback", source_types={"memory"})

    assert memory_id
    assert results
    assert results[0]["source_type"] == "memory"


def test_knowledge_page_ingest_creates_embedding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    note = tmp_path / "note.md"
    note.write_text("# Iris Runtime\nLocal-first agent memory notes.", encoding="utf-8")

    with open_state(config) as db:
        page_id = upsert_file_page(db, note, note.read_text(encoding="utf-8"))
        rows = db.execute(
            "SELECT * FROM memory_embeddings WHERE source_type = 'knowledge_page'"
        ).fetchall()

    assert page_id
    assert rows


def test_llm_rich_extraction_adds_active_graph_relation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    context = _memory_context(
        config,
        openai_client=_FakeOpenAIClient(
            config,
            {
                "relations": [
                    {
                        "subject": "user",
                        "subject_kind": "person",
                        "predicate": "works_on",
                        "object": "Iris",
                        "object_kind": "project",
                        "confidence": 0.88,
                        "sensitive": False,
                        "ambiguous": False,
                        "evidence": "working on Iris",
                    }
                ]
            },
        ),
    )

    saved = _remember({"content": "I am working on Iris", "category": "facts"}, context)

    with open_state(config) as db:
        results = search_facts(db, "works Iris", config=config)

    assert saved.ok
    assert any(
        item["predicate"] == "works_on" and item["object_value"] == "Iris"
        for item in results
    )


def test_llm_sensitive_candidate_is_pending_not_active(tmp_path: Path) -> None:
    config = _config(tmp_path)
    context = _memory_context(
        config,
        openai_client=_FakeOpenAIClient(
            config,
            {
                "relations": [
                    {
                        "subject": "user",
                        "subject_kind": "person",
                        "predicate": "has_ssn",
                        "object": "123",
                        "object_kind": "fact",
                        "confidence": 0.9,
                        "sensitive": True,
                        "ambiguous": False,
                        "evidence": "my SSN is 123",
                    }
                ]
            },
        ),
    )

    saved = _remember(
        {"content": "Remember my SSN is 123", "category": "facts"},
        context,
    )

    with open_state(config) as db:
        pending = db.execute("SELECT * FROM memory_pending_relations").fetchall()
        results = search_facts(db, "SSN 123", config=config)

    assert saved.ok
    assert pending
    assert not any(item["predicate"] == "has_ssn" for item in results)


def test_llm_enrichment_failure_does_not_break_remember(
    tmp_path: Path, monkeypatch
) -> None:
    import iris.memory_llm_extract as llm_extract

    config = _config(tmp_path)

    def _raise(*_args, **_kwargs) -> None:
        raise RuntimeError("graph write failed")

    monkeypatch.setattr(llm_extract, "record_extracted_memory_relations", _raise)
    context = _memory_context(
        config,
        openai_client=_FakeOpenAIClient(
            config,
            {
                "relations": [
                    {
                        "subject": "user",
                        "subject_kind": "person",
                        "predicate": "works_on",
                        "object": "Iris",
                        "object_kind": "project",
                        "confidence": 0.9,
                        "sensitive": False,
                        "ambiguous": False,
                        "evidence": "working on Iris",
                    }
                ]
            },
        ),
    )

    saved = _remember({"content": "I am working on Iris", "category": "facts"}, context)

    with open_state(config) as db:
        rows = db.execute("SELECT * FROM memories").fetchall()

    assert saved.ok
    assert rows


def test_parse_rich_extraction_rejects_low_confidence_and_malformed() -> None:
    malformed = parse_rich_extraction("not json", source_text="I prefer Spotify")
    low_confidence = parse_rich_extraction(
        '{"relations":[{"subject":"user","predicate":"prefers","object":"Spotify","confidence":0.2}]}',
        source_text="I prefer Spotify",
    )

    assert malformed.rejected_count == 1
    assert not low_confidence.active_relations
    assert not low_confidence.pending_candidates
    assert low_confidence.rejected_count == 1


def test_knowledge_page_graphs_chunks_and_relations(tmp_path: Path) -> None:
    config = _config(tmp_path)
    note = tmp_path / "iris.md"
    note.write_text(
        "# Iris\nIris uses SQLite. Iris supports local memory.", encoding="utf-8"
    )

    with open_state(config) as db:
        page_id = upsert_file_page(db, note, note.read_text(encoding="utf-8"))
        result = graph_page(db, page_id)
        chunks = db.execute("SELECT * FROM knowledge_chunks").fetchall()
        related = related_facts(db, "Iris")

    assert result.pages == 1
    assert result.chunks == 1
    assert result.relations >= 2
    assert chunks
    assert any(item["predicate"] == "uses" for item in related)


def test_knowledge_regraph_deactivates_stale_chunk_relations(tmp_path: Path) -> None:
    config = _config(tmp_path)
    note = tmp_path / "iris.md"
    note.write_text("# Iris\nIris uses SQLite.", encoding="utf-8")

    with open_state(config) as db:
        page_id = upsert_file_page(db, note, note.read_text(encoding="utf-8"))
        graph_page(db, page_id)
        note.write_text("# Iris\nIris uses Postgres.", encoding="utf-8")
        upsert_file_page(db, note, note.read_text(encoding="utf-8"))
        graph_page(db, page_id)
        active = related_facts(db, "Iris")
        inactive = related_facts(db, "Iris", include_inactive=True)

    assert any(item["object_value"] == "Postgres" for item in active)
    assert not any(item["object_value"] == "SQLite" for item in active)
    assert any(
        item["object_value"] == "SQLite" and item["active"] is False
        for item in inactive
    )


def test_knowledge_search_includes_graphed_page_facts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    note = tmp_path / "iris.md"
    note.write_text("# Iris\nIris uses SQLite.", encoding="utf-8")

    with open_state(config) as db:
        page_id = upsert_file_page(db, note, note.read_text(encoding="utf-8"))
        rebuild_links(db)
        graph_page(db, page_id)
        pages = search_pages(db, "Iris", limit=5)
        graph = search_facts(db, "SQLite", config=config)

    assert pages
    assert any(item["object_value"] == "SQLite" for item in graph)


def _memory_context(
    config: IrisConfig, *, openai_client: object | None = None
) -> ToolContext:
    return ToolContext(
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=object(),  # type: ignore[arg-type]
        openai_client=openai_client or object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        session_state={},
        config=config,
    )


class _FakeOpenAIClient:
    def __init__(self, config: IrisConfig, payload: dict[str, object]) -> None:
        self.config = config
        self.available = True
        self.payload = payload
        self.responses = self

    def _get_client(self) -> "_FakeOpenAIClient":
        return self

    def create(self, **_kwargs: object) -> str:
        import json

        return json.dumps(self.payload)

    def output_text(self, response: object) -> str:
        return str(response)


def _config(tmp_path: Path) -> IrisConfig:
    return IrisConfig(
        project_root=tmp_path,
        openai_api_key=None,
        google_application_credentials=None,
        agent_name="Iris",
        voice="marin",
        toggle_hotkey="<ctrl>+<space>",
        kill_hotkey="<ctrl>+<alt>+k",
        realtime_model="gpt-realtime-2",
        chat_model="gpt-5.5",
        vision_model="gpt-5.5",
        computer_use_model="computer-use-preview",
        computer_use_environment="browser",
        groq_api_key=None,
        fast_intent_model="compound-mini",
        stt_model="whisper-large-v3-turbo",
        realtime_transcription_model="gpt-4o-mini-transcribe",
        screenshot_interval_seconds=0.5,
        gateway_token=None,
        notify_provider="pushover",
        ntfy_server="https://ntfy.sh",
        ntfy_topic=None,
        ntfy_token=None,
        pushover_token=None,
        pushover_user=None,
        notify_timeout_seconds=30.0,
        email_provider="resend",
        resend_api_key=None,
        email_from=None,
        email_to=None,
        state_db_path=tmp_path / "iris.sqlite3",
        autonomy_level="L1",
        meeting_consent_required=True,
        meeting_retention_days=30,
        listen_seconds=2.0,
        wake_poll_seconds=1.2,
        wake_words=("iris", "hey iris"),
        speak_responses=False,
        max_response_chars=220,
    )
