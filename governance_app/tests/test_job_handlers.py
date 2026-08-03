from __future__ import annotations

from app.core.config import Settings
from app.jobs import handlers


class FakeOpenMetadata:
    def __init__(self) -> None:
        self.closed = False

    def get_confirmed_tag_snapshot(self, *, entity_type: str, entity_fqn: str) -> dict:
        assert entity_type == "table"
        assert entity_fqn == "hive.sales.customers"
        return {
            "entity_tags": [],
            "field_tags": {
                "columns.email": ["PII.Email"],
                "columns.mobile_phone": ["PII.Phone"],
            },
            "tags": ["PII.Email", "PII.Phone"],
            "field_paths": {
                "PII.Email": ["columns.email"],
                "PII.Phone": ["columns.mobile_phone"],
            },
            "all_field_paths": [
                "columns.customer_id",
                "columns.email",
                "columns.mobile_phone",
            ],
        }

    def close(self) -> None:
        self.closed = True


class FakeTagStore:
    def __init__(self) -> None:
        self.closed = False
        self.dry_run = True

    def close(self) -> None:
        self.closed = True


class FakeRangerTagAssignmentService:
    last_kwargs: dict | None = None
    last_tag_store: FakeTagStore | None = None

    def __init__(self, session, settings, tag_store) -> None:
        self.session = session
        self.settings = settings
        type(self).last_tag_store = tag_store

    def sync(self, **kwargs) -> dict:
        type(self).last_kwargs = kwargs
        return {"captured": kwargs}


def test_sync_ranger_tags_refreshes_confirmed_tags_from_openmetadata(
    session,
    monkeypatch,
) -> None:
    fake_om = FakeOpenMetadata()
    fake_tag_store = FakeTagStore()
    settings = Settings(
        _env_file=None,
        openmetadata_enabled=True,
        ranger_enabled=True,
        ranger_dry_run=True,
    )

    monkeypatch.setattr(
        handlers,
        "_auto_tag_openmetadata_client",
        lambda _settings: fake_om,
    )
    monkeypatch.setattr(
        handlers,
        "_ranger_tag_store_client",
        lambda _settings: fake_tag_store,
    )
    monkeypatch.setattr(
        handlers,
        "RangerTagAssignmentService",
        FakeRangerTagAssignmentService,
    )

    result = handlers.handle_sync_ranger_tags(
        session,
        settings,
        {
            "entity_type": "table",
            "entity_fqn": "hive.sales.customers",
            "classification_run_id": None,
            "correlation_id": "corr",
        },
    )

    assert fake_om.closed is True
    assert fake_tag_store.closed is True
    assert FakeRangerTagAssignmentService.last_kwargs == {
        "entity_type": "table",
        "entity_fqn": "hive.sales.customers",
        "entity_tags": [],
        "field_tags": {
            "columns.email": ["PII.Email"],
            "columns.mobile_phone": ["PII.Phone"],
        },
        "classification_run_id": None,
        "correlation_id": "corr",
    }
    assert result["captured"]["field_tags"]["columns.email"] == ["PII.Email"]


def test_reconcile_handler_delegates_to_sync_ranger_tags(
    session,
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        ranger_enabled=True,
        ranger_dry_run=True,
    )
    captured: dict = {}

    def fake_sync(session_arg, settings_arg, payload_arg):
        captured["session"] = session_arg
        captured["settings"] = settings_arg
        captured["payload"] = payload_arg
        return {"status": "delegated"}

    monkeypatch.setattr(handlers, "handle_sync_ranger_tags", fake_sync)

    payload = {
        "entity_type": "table",
        "entity_fqn": "hive.sales.customers",
        "tags": ["PII.Email"],
        "field_paths": {"PII.Email": ["columns.email"]},
        "classification_run_id": "run-old",
        "correlation_id": "corr-old",
    }

    result = handlers.handle_reconcile_ranger(session, settings, payload)

    assert result == {"status": "delegated"}
    assert captured["session"] is session
    assert captured["settings"] is settings
    assert captured["payload"] is payload
