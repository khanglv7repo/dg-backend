from __future__ import annotations

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.repositories.data_access_policy import DataAccessPolicyRepository
from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.policy_compiler import PolicyCompiler
from app.services.policy_query import PolicyQueryService
import pytest

SETTINGS = Settings(app_env="test", ranger_service_name="dev_trino")


def policy(
    *,
    user: str = "alice",
    decision: str = "ALLOW",
    resource_table: str = "customer",
    row_filter: str | None = "region = 'VN'",
) -> dict:
    return {
        "subjects": [{"type": "USER", "name": user}],
        "resource": {
            "catalog": "dev",
            "schema": "sales",
            "table": resource_table,
        },
        "access": {"select": decision},
        "masks": {"phone": "MASK"},
        "row_filter": row_filter,
    }


def _active(session, key: str, document: dict):
    parsed = LogicalDataAccessPolicy.model_validate(document)
    repo = DataAccessPolicyRepository(session)
    row = repo.create_version(
        policy_key=key,
        logical_policy=parsed.normalized_document(),
        checksum=parsed.checksum(),
        created_by="test",
    )
    repo.activate(row, activated_at=row.created_at)
    compiled = PolicyCompiler(ranger_service_name="dev_trino").compile(
        policy_key=key,
        version=row.version,
        logical_policy=parsed,
    )
    repo.upsert_desired_projections(
        policy_version_id=row.id,
        projections=compiled,
        reset_status=True,
    )
    return row


def test_policy_read_versions_and_durable_sync_status(session) -> None:
    with session.begin():
        first = _active(session, "policy-a", policy())
        parsed = LogicalDataAccessPolicy.model_validate(policy(decision="DENY"))
        repo = DataAccessPolicyRepository(session)
        second = repo.create_version(
            policy_key="policy-a",
            logical_policy=parsed.normalized_document(),
            checksum=parsed.checksum(),
            created_by="test",
        )
    service = PolicyQueryService(session, SETTINGS)

    active = service.get_policy(policy_key="policy-a")
    explicit = service.get_policy(policy_key="policy-a", version=second.version)
    versions = service.list_policy_versions(policy_key="policy-a")
    sync = service.get_ranger_sync_status(policy_key="policy-a")

    assert active["id"] == str(first.id)
    assert explicit["status"] == "DRAFT"
    assert [item["version"] for item in versions] == [1, 2]
    assert {item["sync_status"] for item in sync["projections"]} == {"PENDING"}


def test_conflict_is_bounded_to_exact_resource_and_subject(session) -> None:
    with session.begin():
        _active(session, "existing", policy(decision="ALLOW", row_filter="region = 'VN'"))

    service = PolicyQueryService(session, SETTINGS)
    opposite = service.check_policy_conflict(
        policy_key="candidate",
        logical_policy=policy(decision="DENY", row_filter="country = 'VN'"),
    )
    unrelated = service.check_policy_conflict(
        policy_key="other",
        logical_policy=policy(decision="DENY", resource_table="orders"),
    )

    assert opposite["conflict"] is True
    assert opposite["conflicts"][0]["type"] == "OPPOSITE_ACCESS_DECISION"
    assert opposite["requires_review"] is True
    assert opposite["warnings"][0]["type"] == "ROW_FILTER_OVERLAP_REQUIRES_REVIEW"
    assert unrelated["conflict"] is False
    assert unrelated["warnings"] == []


def test_get_policy_not_found_and_sync_status_preserves_runtime_states(session) -> None:
    service = PolicyQueryService(session, SETTINGS)
    with pytest.raises(NotFoundError):
        service.get_policy(policy_key="missing")
    session.rollback()

    with session.begin():
        row = _active(session, "policy-status", policy())
        projections = DataAccessPolicyRepository(session).list_projections(row.id)
        projections[0].sync_status = "MISMATCH"
        projections[0].last_error = "read-back mismatch"
        if len(projections) > 1:
            projections[1].sync_status = "SUPERSEDED"
        session.flush()

    status = service.get_ranger_sync_status(policy_key="policy-status")
    states = {item["sync_status"] for item in status["projections"]}
    assert "MISMATCH" in states
    if len(status["projections"]) > 1:
        assert "SUPERSEDED" in states
    assert any(item["last_error"] == "read-back mismatch" for item in status["projections"])
