from __future__ import annotations

import pytest

from app.core.errors import NotFoundError
from app.models.audit import AuditEvent
from app.services.service_mapping import ServiceMappingService


def test_service_mapping_exact_resolve_update_and_no_fuzzy_guess(session) -> None:
    with session.begin():
        created = ServiceMappingService(session).update(
            om_service_name="financial_postgres",
            trino_catalog="financial",
            ranger_service_name="dev_trino",
            ranger_tag_service_name="dev_tag",
            environment="local",
            enabled=True,
            actor_id="backend-mcp",
            actor_name="Backend MCP",
            reason="R5 fixture",
        )

    assert created["status"] == "RESOLVED"
    assert created["om_service_name"] == "financial_postgres"

    resolved = ServiceMappingService(session).resolve(
        om_service_name="financial_postgres",
        environment="local",
    )
    assert resolved["id"] == created["id"]
    assert resolved["trino_catalog"] == "financial"
    assert resolved["ranger_service_name"] == "dev_trino"

    # Exact service/environment identity only. No substring/string-similarity fallback.
    with pytest.raises(NotFoundError) as exc_info:
        ServiceMappingService(session).resolve(
            om_service_name="financial",
            environment="local",
        )
    assert exc_info.value.details["status"] == "UNRESOLVED"

    audit = session.query(AuditEvent).filter_by(action="SERVICE_MAPPING_CREATED").one()
    assert audit.actor_id == "backend-mcp"


def test_disabled_mapping_is_unresolved(session) -> None:
    with session.begin():
        ServiceMappingService(session).update(
            om_service_name="warehouse",
            trino_catalog="warehouse",
            ranger_service_name="dev_trino",
            ranger_tag_service_name=None,
            environment="local",
            enabled=False,
            actor_id="backend-mcp",
            actor_name="Backend MCP",
        )

    with pytest.raises(NotFoundError) as exc_info:
        ServiceMappingService(session).resolve(
            om_service_name="warehouse",
            environment="local",
        )
    assert exc_info.value.details["status"] == "UNRESOLVED"
