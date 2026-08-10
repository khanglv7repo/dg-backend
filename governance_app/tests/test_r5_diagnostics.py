from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest

from app.clients.ranger import RangerClient
from app.core.errors import NotFoundError
from app.repositories.audit import AuditRepository
from app.repositories.classification_execution import ClassificationExecutionRepository
from app.services.audit_query import AuditQueryService
from app.services.ranger_inspection import RangerInspectionService
from app.services.workflow_query import WorkflowQueryService


def test_workflow_status_existing_and_not_found(session) -> None:
    with session.begin():
        execution = ClassificationExecutionRepository(session).create(
            event_id="evt-r5-workflow",
            entity_type="table",
            entity_fqn="dev.sales.customer",
            status="WAITING_AI",
            outcome="NO_MATCH",
            correlation_id="corr-r5",
        )

    result = WorkflowQueryService(session).get(str(execution.id))
    assert result["source"] == "classification_execution"
    assert result["status"] == "WAITING_AI"
    assert "suggestions" not in result
    assert "evidence" not in result

    with pytest.raises(NotFoundError):
        WorkflowQueryService(session).get("00000000-0000-0000-0000-000000000001")


def test_audit_summary_filters_and_enforces_hard_limit(session) -> None:
    with session.begin():
        repo = AuditRepository(session)
        for index in range(105):
            repo.record(
                actor_id="test",
                actor_name="Test",
                action="POLICY_EVENT" if index % 2 == 0 else "OTHER_EVENT",
                object_type="data-access-policy-version",
                object_id=str(index),
                correlation_id=None,
                details={"policy_key": "policy-a" if index < 103 else "policy-b"},
            )

    result = AuditQueryService(session).summary(
        object_type="data-access-policy-version",
        policy_key="policy-a",
        action="POLICY_EVENT",
        since=datetime(2000, 1, 1, tzinfo=UTC),
        limit=1000,
    )
    assert result["hard_limit"] == 100
    assert result["limit"] == 100
    assert result["returned"] <= 100
    assert all(item["action"] == "POLICY_EVENT" for item in result["records"])
    assert all(item["details"]["policy_key"] == "policy-a" for item in result["records"])


def test_ranger_inspection_is_read_only_and_marks_managed_state(session) -> None:
    ranger = create_autospec(RangerClient, instance=True)
    ranger.service_name = "dev_trino"
    managed = {
        "id": 43,
        "name": "dg-r4-policy-access",
        "policyType": 0,
        "service": "dev_trino",
        "isEnabled": True,
        "description": "Policy | managed-by=dg-backend;policy-key=policy-a;",
        "resources": {"catalog": {"values": ["dev"]}},
    }
    unmanaged = {**managed, "id": 44, "description": "manual policy"}
    ranger.find_by_name.side_effect = lambda name: (
        managed if name == "dg-r4-policy-access" else unmanaged
    )
    ranger.owns_policy.side_effect = lambda document, policy_key: (
        document is managed and policy_key == "policy-a"
    )
    ranger.find_user.return_value = {"id": 1, "name": "alice"}
    ranger.find_group.return_value = None

    service = RangerInspectionService(session, ranger_client=ranger)
    owned = service.inspect(
        kind="policy",
        name="dg-r4-policy-access",
        policy_key="policy-a",
    )
    other = service.inspect(kind="policy", name="manual", policy_key="policy-a")
    user = service.inspect(kind="user", name="alice")
    group = service.inspect(kind="group", name="missing")

    assert owned["managed_by_backend"] is True
    assert owned["owned_for_policy_key"] is True
    assert other["managed_by_backend"] is False
    assert other["owned_for_policy_key"] is False
    assert user["exists"] is True
    assert group["exists"] is False
    ranger.reconcile_document.assert_not_called()


def test_ranger_health_is_bounded_and_does_not_expose_service_configs(session) -> None:
    ranger = create_autospec(RangerClient, instance=True)
    ranger.service_name = "dev_trino"
    ranger.health.return_value = {
        "id": 1,
        "name": "dev_trino",
        "type": "trino",
        "isEnabled": True,
        "tagService": "dev_tag",
        "policyVersion": 76,
        "policyUpdateTime": 123456,
        "tagVersion": 28,
        "tagUpdateTime": 123457,
        "configs": {
            "jdbc.url": "jdbc:trino://trino:8080",
            "username": "trino",
            "password": "must-not-leak",
        },
        "createdBy": "Admin",
        "updatedBy": "Admin",
        "description": "internal service configuration",
    }

    result = RangerInspectionService(session, ranger_client=ranger).inspect(kind="health")

    assert result == {
        "kind": "health",
        "service_name": "dev_trino",
        "state": {
            "id": 1,
            "name": "dev_trino",
            "type": "trino",
            "isEnabled": True,
            "tagService": "dev_tag",
            "policyVersion": 76,
            "policyUpdateTime": 123456,
            "tagVersion": 28,
            "tagUpdateTime": 123457,
        },
    }
    encoded = repr(result)
    assert "configs" not in encoded
    assert "password" not in encoded
    assert "jdbc.url" not in encoded
    assert "createdBy" not in encoded
