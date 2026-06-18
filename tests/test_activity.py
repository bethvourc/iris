from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Iterator

import pytest

from iris.activity import ActivityRequestError, activity_detail, activity_feed
from iris.gateway import GatewayService
from iris.state import open_state

NOW = datetime(2026, 6, 10, 18, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    config = SimpleNamespace(state_db_path=tmp_path / "iris.sqlite3")
    with open_state(config) as connection:
        yield connection


def _add_session(
    db: sqlite3.Connection,
    session_id: str,
    *,
    updated_at: datetime,
    channel: str = "voice",
    title: str = "Voice session",
    messages: tuple[tuple[str, str], ...] = (),
) -> None:
    db.execute(
        """
        INSERT INTO agent_sessions (
          session_id, channel, title, status, created_at, updated_at
        ) VALUES (?, ?, ?, 'active', ?, ?)
        """,
        (session_id, channel, title, _iso(updated_at), _iso(updated_at)),
    )
    for index, (role, content) in enumerate(messages):
        db.execute(
            """
            INSERT INTO agent_messages (
              message_id, session_id, role, content, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"{session_id}-m{index}",
                session_id,
                role,
                content,
                _iso(updated_at + timedelta(seconds=index)),
            ),
        )
    db.commit()


def _add_run(
    db: sqlite3.Connection,
    run_id: str,
    *,
    updated_at: datetime,
    status: str = "done",
    message: str = "Did the thing",
) -> None:
    db.execute(
        """
        INSERT INTO agent_runs (
          run_id, channel, status, message, created_at, updated_at
        ) VALUES (?, 'voice', ?, ?, ?, ?)
        """,
        (run_id, status, message, _iso(updated_at), _iso(updated_at)),
    )
    db.commit()


def _add_meeting(
    db: sqlite3.Connection,
    meeting_id: str,
    *,
    started_at: datetime,
    stopped_at: datetime | None,
    summary: str | None = None,
) -> None:
    summary_json = f'{{"summary": "{summary}"}}' if summary else "{}"
    db.execute(
        """
        INSERT INTO meetings (
          meeting_id, title, mode, status, consent_required, disclosure_spoken,
          retention_days, started_at, stopped_at, summary_json
        ) VALUES (?, 'Team sync', 'silent', 'stopped', 1, 1, 30, ?, ?, ?)
        """,
        (
            meeting_id,
            _iso(started_at),
            _iso(stopped_at) if stopped_at else None,
            summary_json,
        ),
    )
    db.commit()


def test_empty_feed_shape(db: sqlite3.Connection) -> None:
    feed = activity_feed(db, now=NOW)
    assert feed == {
        "stats": {
            "sessions_this_week": 0,
            "runs_completed": 0,
            "runs_failed": 0,
            "last_active_at": None,
        },
        "days": [],
        "next_cursor": None,
    }


def test_mixed_kinds_are_merged_newest_first(db: sqlite3.Connection) -> None:
    _add_session(db, "s1", updated_at=NOW - timedelta(hours=3), channel="voice")
    _add_session(db, "s2", updated_at=NOW - timedelta(hours=1), channel="gateway")
    _add_run(db, "r1", updated_at=NOW - timedelta(hours=2))
    _add_meeting(
        db,
        "m1",
        started_at=NOW - timedelta(hours=5),
        stopped_at=NOW - timedelta(hours=4),
        summary="Discussed roadmap",
    )

    feed = activity_feed(db, now=NOW)
    items = [item for day in feed["days"] for item in day["items"]]
    assert [item["id"] for item in items] == ["ses-s2", "run-r1", "ses-s1", "mtg-m1"]
    assert [item["kind"] for item in items] == [
        "message",
        "run",
        "voice_session",
        "meeting",
    ]
    assert items[3]["preview"] == "Discussed roadmap"
    assert all(item["time"].endswith("Z") for item in items)


def test_day_grouping_uses_client_timezone_with_labels(
    db: sqlite3.Connection,
) -> None:
    # 03:00 UTC on June 10 is 23:00 June 9 in New York
    _add_session(db, "s-late", updated_at=NOW.replace(hour=3))
    _add_session(db, "s-today", updated_at=NOW - timedelta(hours=1))

    feed = activity_feed(db, tz="America/New_York", now=NOW)
    assert [day["date"] for day in feed["days"]] == ["2026-06-10", "2026-06-09"]
    assert [day["label"] for day in feed["days"]] == ["today", "yesterday"]
    assert feed["days"][1]["items"][0]["id"] == "ses-s-late"

    # the same instants group together in UTC
    feed_utc = activity_feed(db, tz="UTC", now=NOW)
    assert [day["date"] for day in feed_utc["days"]] == ["2026-06-10"]


def test_pagination_pages_through_without_overlap(db: sqlite3.Connection) -> None:
    for index in range(5):
        _add_run(db, f"r{index}", updated_at=NOW - timedelta(minutes=index))

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        feed = activity_feed(db, limit=2, cursor=cursor, now=NOW)
        seen.extend(
            item["id"] for day in feed["days"] for item in day["items"]
        )
        pages += 1
        cursor = feed["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert seen == ["run-r0", "run-r1", "run-r2", "run-r3", "run-r4"]


def test_days_window_excludes_older_items(db: sqlite3.Connection) -> None:
    _add_run(db, "recent", updated_at=NOW - timedelta(days=2))
    _add_run(db, "ancient", updated_at=NOW - timedelta(days=30))

    feed = activity_feed(db, days=7, now=NOW)
    ids = [item["id"] for day in feed["days"] for item in day["items"]]
    assert ids == ["run-recent"]


def test_bounds_clamp_instead_of_failing(db: sqlite3.Connection) -> None:
    feed = activity_feed(db, days=10_000, limit=10_000, now=NOW)
    assert feed["days"] == []


def test_invalid_timezone_and_cursor_raise_request_errors(
    db: sqlite3.Connection,
) -> None:
    with pytest.raises(ActivityRequestError):
        activity_feed(db, tz="Mars/Olympus_Mons", now=NOW)
    with pytest.raises(ActivityRequestError):
        activity_feed(db, cursor="garbage", now=NOW)


def test_session_preview_is_last_message(db: sqlite3.Connection) -> None:
    _add_session(
        db,
        "s1",
        updated_at=NOW - timedelta(hours=1),
        messages=(("user", "first"), ("assistant", "the final word")),
    )
    feed = activity_feed(db, now=NOW)
    item = feed["days"][0]["items"][0]
    assert item["preview"] == "the final word"


def test_run_status_mapping_to_contract_set(db: sqlite3.Connection) -> None:
    _add_run(db, "r-queued", status="queued", updated_at=NOW - timedelta(minutes=1))
    _add_run(db, "r-blocked", status="blocked", updated_at=NOW - timedelta(minutes=2))

    feed = activity_feed(db, now=NOW)
    statuses = {
        item["id"]: item["status"]
        for day in feed["days"]
        for item in day["items"]
    }
    assert statuses == {"run-r-queued": "running", "run-r-blocked": "blocked"}


def test_stats_counts(db: sqlite3.Connection) -> None:
    _add_session(db, "s1", updated_at=NOW - timedelta(days=1))
    _add_session(db, "s-old", updated_at=NOW - timedelta(days=20))
    _add_run(db, "r1", status="done", updated_at=NOW - timedelta(days=2))
    _add_run(db, "r2", status="failed", updated_at=NOW - timedelta(days=3))

    stats = activity_feed(db, now=NOW)["stats"]
    assert stats["sessions_this_week"] == 1
    assert stats["runs_completed"] == 1
    assert stats["runs_failed"] == 1
    assert stats["last_active_at"] == "2026-06-09T18:00:00Z"


def test_session_detail_has_ordered_transcript(db: sqlite3.Connection) -> None:
    _add_session(
        db,
        "s1",
        updated_at=NOW - timedelta(hours=1),
        messages=(("user", "hello"), ("assistant", "hi there")),
    )
    detail = activity_detail(db, "ses-s1")
    assert detail is not None
    assert detail["kind"] == "voice_session"
    assert [(t["role"], t["text"]) for t in detail["transcript"]] == [
        ("user", "hello"),
        ("assistant", "hi there"),
    ]


def test_run_detail_links_run_events(db: sqlite3.Connection) -> None:
    _add_run(db, "r1", updated_at=NOW, message="Summarize my inbox")
    detail = activity_detail(db, "run-r1")
    assert detail is not None
    assert detail["run"] == {"run_id": "r1", "events_url": "/runs/r1/events"}
    assert detail["title"] == "Summarize my inbox"


def test_detail_unknown_ids_return_none(db: sqlite3.Connection) -> None:
    assert activity_detail(db, "ses-missing") is None
    assert activity_detail(db, "bogus-id") is None


# ------------------------------------------------------------- gateway


def test_gateway_activity_routes(tmp_path: Path) -> None:
    config = SimpleNamespace(
        state_db_path=tmp_path / "iris.sqlite3",
        gateway_token="tok",
        agent_name="Iris",
    )
    service = GatewayService(config=config, router_factory=lambda: None)

    assert service.authorize("/activity", None) is False
    assert service.authorize("/activity", "Bearer tok") is True

    status, payload = service.handle_get("/activity", {})
    assert status == 200
    assert payload["days"] == [] and payload["next_cursor"] is None

    status, payload = service.handle_get("/activity", {"tz": ["Mars/Base"]})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"

    status, payload = service.handle_get("/activity/ses-missing", {})
    assert status == 404
    assert payload["error"]["code"] == "not_found"
