from __future__ import annotations

import json
from pathlib import Path

import pytest

from iris.audit import list_audit
from iris.config import IrisConfig
from iris.gateway import GatewayService
from iris.state import open_state

SENTINEL_SECRET = "sk-test-secret-sentinel-value"

LAYERED_ENV_VARS = (
    "IRIS_WAKE_WORDS",
    "IRIS_VOICE",
    "IRIS_REALTIME_MODEL",
    "IRIS_NOTIFY_PROVIDER",
    "IRIS_WAKE_WORD_ENABLED",
)


@pytest.fixture()
def service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> GatewayService:
    monkeypatch.setenv("IRIS_STATE_DB", str(tmp_path / "state" / "iris.sqlite3"))
    monkeypatch.setenv("IRIS_GATEWAY_TOKEN", "settings-test-token")
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_SECRET)
    for name in LAYERED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config = IrisConfig.from_env(tmp_path)
    return GatewayService(config=config, router_factory=lambda: None)


def test_settings_routes_require_auth(service: GatewayService) -> None:
    assert service.authorize("/settings", None) is False
    assert service.authorize("/settings", "Bearer settings-test-token") is True


def test_get_settings_shape_and_sources(service: GatewayService) -> None:
    status, payload = service.handle_get("/settings", {})
    assert status == 200

    voice = payload["settings"]["voice"]
    assert voice == {"value": "marin", "source": "default", "mutable": True}
    wake_words = payload["settings"]["wake_words"]
    assert wake_words["value"] == ["iris", "hey iris"]
    assert payload["settings"]["wake_word_enabled"]["value"] is False

    assert payload["secrets"]["openai_api_key"] == {"is_set": True}
    assert payload["secrets"]["pushover_token"]["is_set"] in (True, False)


def test_secret_values_never_serialize(service: GatewayService) -> None:
    _, get_payload = service.handle_get("/settings", {})
    _, put_payload = service.handle_put("/settings", {"voice": "cedar"})

    for payload in (get_payload, put_payload):
        assert SENTINEL_SECRET not in json.dumps(payload)


def test_put_persists_and_reloads_config(service: GatewayService) -> None:
    status, payload = service.handle_put(
        "/settings", {"voice": "cedar", "wake_word_enabled": True}
    )
    assert status == 200
    assert payload["settings"]["voice"] == {
        "value": "cedar",
        "source": "settings",
        "mutable": True,
    }
    assert payload["settings"]["wake_word_enabled"]["value"] is True

    # the gateway's own config was reloaded, so new sessions see the change
    assert service.config.voice == "cedar"
    assert service.config.wake_word_enabled is True

    # and it survives a fresh GET (file-backed, not in-memory)
    _, get_payload = service.handle_get("/settings", {})
    assert get_payload["settings"]["voice"]["value"] == "cedar"


def test_put_unknown_key_is_rejected_with_envelope(service: GatewayService) -> None:
    status, payload = service.handle_put("/settings", {"theme": "dark"})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert "theme" in payload["error"]["message"]


def test_put_invalid_value_is_rejected(service: GatewayService) -> None:
    status, payload = service.handle_put("/settings", {"wake_words": []})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_put_env_managed_key_returns_409(
    service: GatewayService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IRIS_VOICE", "alloy")
    status, payload = service.handle_put("/settings", {"voice": "cedar"})
    assert status == 409
    assert payload["error"]["code"] == "setting_env_managed"
    assert "IRIS_VOICE" in payload["error"]["message"]

    _, get_payload = service.handle_get("/settings", {})
    assert get_payload["settings"]["voice"]["source"] == "env"
    assert get_payload["settings"]["voice"]["mutable"] is False


def test_put_empty_body_is_rejected(service: GatewayService) -> None:
    status, payload = service.handle_put("/settings", {})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_put_unknown_path_is_404(service: GatewayService) -> None:
    status, _ = service.handle_put("/nope", {})
    assert status == 404


def test_put_is_audit_logged_by_key_only(service: GatewayService) -> None:
    service.handle_put("/settings", {"voice": "cedar"})

    with open_state(service.config) as db:
        entries = list_audit(db, limit=5)
    entry = next(e for e in entries if e["tool"] == "settings.update")
    serialized = json.dumps(entry)
    assert "voice" in serialized
    assert "cedar" not in serialized  # keys logged, values not


def test_deciding_orphaned_approval_succeeds(service: GatewayService) -> None:
    """An approval whose run is gone (e.g. daemon restart) must still be
    decidable — notification actions hit this path."""
    from iris.state import open_state

    with open_state(service.config) as db:
        db.execute(
            """
            INSERT INTO approvals (
              approval_id, run_id, action_name, risk, status, preview,
              created_at, expires_at
            ) VALUES ('abc123def456', 'gone-run', 'system.run', 'sensitive',
                      'pending', 'Run something', '2026-06-12T00:00:00+00:00',
                      '2026-06-12T01:00:00+00:00')
            """
        )
        db.commit()

    status, payload = service.handle_post("/approvals/abc123def456/deny", {})
    assert (status, payload) == (200, {"ok": True, "status": "denied"})

    status, payload = service.handle_get("/approvals", {})
    assert payload["approvals"] == []
