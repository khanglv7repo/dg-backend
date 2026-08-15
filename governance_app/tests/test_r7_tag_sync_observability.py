from unittest.mock import create_autospec, patch

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings
from app.db.base import Base
from app.mcp import backend_mcp_server
from app.models.event_inbox import EventInbox
from app.models.tag_sync_state import TagSyncState


SETTINGS = Settings(
    app_env="test",
    ranger_enabled=True,
    ranger_service_name="dev_trino",
    ranger_dry_run=False,
    mcp_enabled=True,
)


def test_mcp_tag_sync_observability_uses_durable_state_and_current_ranger_readback() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    entity_fqn = "dev.sales.customer"

    with factory() as session:
        with session.begin():
            session.add(
                EventInbox(
                    event_id="evt-tag-observe",
                    event_type="entityUpdated",
                    entity_type="table",
                    entity_fqn=entity_fqn,
                    payload={"must_not": "be_exposed"},
                    purposes=["TAG_SYNC"],
                    dispatched_purposes=["TAG_SYNC"],
                    dispatched_tasks={"TAG_SYNC": "celery-tag-1"},
                    status="PROCESSED",
                )
            )
            session.add(
                TagSyncState(
                    entity_type="table",
                    entity_fqn=entity_fqn,
                    status="SYNCHRONIZED",
                    checksum="a" * 64,
                    details={
                        "entity_tags": ["PII.Sensitive"],
                        "field_tags": {"phone": ["PII.Phone"]},
                    },
                )
            )

    tag_store = create_autospec(RangerTagStoreClient, instance=True)
    tag_store.read_actual_state.return_value = {
        ("$entity", "PII.Sensitive"),
        ("phone", "PII.Phone"),
    }

    with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
        backend_mcp_server, "get_settings", return_value=SETTINGS
    ), patch.object(
        backend_mcp_server,
        "create_ranger_tag_store_client",
        return_value=tag_store,
    ):
        result = backend_mcp_server.get_tag_sync_observability.fn(
            entity_type="table", entity_fqn=entity_fqn
        )

    assert result["webhook"]["received"] is True
    assert result["tag_sync_dispatch"]["dispatched"] is True
    assert "not Ranger synchronization" in result["tag_sync_dispatch"]["note"]
    assert result["tag_sync_state"]["status"] == "SYNCHRONIZED"
    assert result["ranger_read_back"]["matches_durable_snapshot"] is True
    assert result["synchronized"] is True
    assert "payload" not in str(result)

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_mcp_tag_sync_observability_does_not_equate_processed_with_synchronized() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    entity_fqn = "dev.sales.not_synced"

    with factory() as session:
        with session.begin():
            session.add(
                EventInbox(
                    event_id="evt-dispatched-only",
                    event_type="entityUpdated",
                    entity_type="table",
                    entity_fqn=entity_fqn,
                    payload={},
                    purposes=["TAG_SYNC"],
                    dispatched_purposes=["TAG_SYNC"],
                    dispatched_tasks={"TAG_SYNC": "celery-tag-2"},
                    status="PROCESSED",
                )
            )

    tag_store = create_autospec(RangerTagStoreClient, instance=True)
    tag_store.read_actual_state.return_value = set()

    with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
        backend_mcp_server, "get_settings", return_value=SETTINGS
    ), patch.object(
        backend_mcp_server,
        "create_ranger_tag_store_client",
        return_value=tag_store,
    ):
        result = backend_mcp_server.get_tag_sync_observability.fn(
            entity_type="table", entity_fqn=entity_fqn
        )

    assert result["webhook"]["events"][0]["status"] == "PROCESSED"
    assert result["tag_sync_dispatch"]["dispatched"] is True
    assert result["tag_sync_state"] == {"found": False}
    assert result["synchronized"] is False

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_mcp_activation_rejects_unconfirmed_before_authority_or_ranger_access() -> None:
    with patch.object(backend_mcp_server, "get_settings") as get_settings, patch.object(
        backend_mcp_server, "build_resource_ranger_client"
    ) as build_ranger:
        with pytest.raises(ToolError, match="CONFIRMATION_REQUIRED"):
            backend_mcp_server.activate_policy_version.fn(
                policy_key="sales.customer",
                version=1,
                confirmed=False,
            )

    get_settings.assert_not_called()
    build_ranger.assert_not_called()
