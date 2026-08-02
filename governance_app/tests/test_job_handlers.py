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


class FakeRanger:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.dry_run = bool(kwargs.get("dry_run"))


class FakePolicySyncService:
    last_kwargs: dict | None = None

    def __init__(self, session, settings, ranger) -> None:
        self.session = session
        self.settings = settings
        self.ranger = ranger

    def sync(self, **kwargs) -> dict:
        type(self).last_kwargs = kwargs
        return {"captured": kwargs}


def test_reconcile_handler_refreshes_confirmed_tags_from_openmetadata(
    session,
    monkeypatch,
) -> None:
    fake_om = FakeOpenMetadata()
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
    monkeypatch.setattr(handlers, "RangerClient", FakeRanger)
    monkeypatch.setattr(handlers, "PolicySyncService", FakePolicySyncService)

    result = handlers.handle_reconcile_ranger(
        session,
        settings,
        {
            "entity_type": "table",
            "entity_fqn": "hive.sales.customers",
            "refresh_confirmed_tags": True,
            "classification_run_id": None,
            "correlation_id": "corr",
        },
    )

    assert fake_om.closed is True
    assert FakePolicySyncService.last_kwargs == {
        "entity_fqn": "hive.sales.customers",
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
        "classification_run_id": None,
        "correlation_id": "corr",
    }
    assert result["captured"]["tags"] == ["PII.Email", "PII.Phone"]


def test_reconcile_handler_keeps_legacy_explicit_payload_compatible(
    session,
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        ranger_enabled=True,
        ranger_dry_run=True,
    )

    monkeypatch.setattr(handlers, "RangerClient", FakeRanger)
    monkeypatch.setattr(handlers, "PolicySyncService", FakePolicySyncService)

    handlers.handle_reconcile_ranger(
        session,
        settings,
        {
            "entity_fqn": "hive.sales.customers",
            "tags": ["PII.Email"],
            "field_paths": {"PII.Email": ["columns.email"]},
            "classification_run_id": "run-old",
            "correlation_id": "corr-old",
        },
    )

    assert FakePolicySyncService.last_kwargs == {
        "entity_fqn": "hive.sales.customers",
        "tags": ["PII.Email"],
        "field_paths": {"PII.Email": ["columns.email"]},
        "all_field_paths": None,
        "classification_run_id": "run-old",
        "correlation_id": "corr-old",
    }
