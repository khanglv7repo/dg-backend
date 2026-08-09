from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from app.clients.ranger import RangerClient, normalize_policy
from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.data_access_policy import DataAccessPolicyService
from app.services.policy_reconciliation import PolicyReconciliationService


def settings() -> Settings:
    return Settings(
        app_env="test",
        ranger_enabled=True,
        ranger_service_name="dev_trino",
        ranger_dry_run=False,
    )


def logical_policy(*, phone_mask: bool = True) -> dict:
    return {
        "subjects": [{"type": "USER", "name": "alice"}],
        "resource": {"catalog": "dev", "schema": "sales", "table": "customer"},
        "access": {"select": "ALLOW", "insert": "DENY"},
        "masks": {"phone": "MASK"} if phone_mask else {},
        "row_filter": "region = 'VN'",
    }


class RangerTransport:
    """HTTP boundary fake; policy business behavior stays in production code."""

    def __init__(self) -> None:
        self.policies: dict[str, dict] = {}
        self.writes: list[tuple[str, str]] = []
        self.next_id = 100
        self.force_post_read_mismatch = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/service/dev_trino/policy/" in path:
            name = path.rsplit("/", 1)[-1]
            policy = self.policies.get(name)
            return (
                httpx.Response(200, json=deepcopy(policy))
                if policy is not None
                else httpx.Response(404, json={"message": "not found"})
            )
        if request.method == "POST" and path.endswith("/policy/apply"):
            payload = json.loads(request.content)
            self.next_id += 1
            payload["id"] = self.next_id
            payload["guid"] = f"guid-{self.next_id}"
            self.policies[payload["name"]] = deepcopy(payload)
            self.writes.append(("POST", payload["name"]))
            if self.force_post_read_mismatch:
                self.policies[payload["name"]]["isAuditEnabled"] = False
            return httpx.Response(200, json=payload)
        if request.method == "PUT" and "/policy/" in path:
            payload = json.loads(request.content)
            name = payload["name"]
            existing = self.policies.get(name, {})
            payload.setdefault("guid", existing.get("guid", f"guid-{payload['id']}"))
            self.policies[name] = deepcopy(payload)
            self.writes.append(("PUT", name))
            return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected Ranger request: {request.method} {path}")


def ranger(transport: RangerTransport) -> RangerClient:
    client = RangerClient(
        base_url="http://ranger:6080/service/public/v2/api",
        username="admin",
        password="secret",
        service_name="dev_trino",
        dry_run=False,
    )
    client.client.close()
    client.client = httpx.Client(
        base_url="http://ranger:6080/service/public/v2/api",
        transport=httpx.MockTransport(transport.handler),
    )
    return client


def activate(session, client: RangerClient, *, key: str, policy: dict):
    # Activation subject checks are true external reads. These tests focus on
    # projection reconciliation, so the boundary methods are fixed to an
    # already-discovered user without duplicating compiler/controller logic.
    client.user_exists = lambda name: name == "alice"  # type: ignore[method-assign]
    client.group_exists = lambda _name: False  # type: ignore[method-assign]
    service = DataAccessPolicyService(session, settings(), ranger_client=client)
    with session.begin():
        version = service.create_version(
            policy_key=key,
            logical_policy=policy,
            actor_id="admin",
            actor_name="Admin",
        )
    with session.begin():
        target = service.read_activation_target(
            policy_key=key,
            version=version.version,
        )
    validation = service.validate_activation_subjects(target)
    with session.begin():
        service.activate_version(
            validation=validation,
            actor_id="admin",
            actor_name="Admin",
        )
    return version


def test_reconcile_create_read_back_repeat_no_change(session) -> None:
    transport = RangerTransport()
    client = ranger(transport)
    try:
        version = activate(
            session,
            client,
            key="sales.customer",
            policy=logical_policy(),
        )
        controller = PolicyReconciliationService(session, settings(), ranger_client=client)
        first = controller.reconcile(policy_version_id=str(version.id))
        session.commit()
        assert first["status"] == "SYNCHRONIZED"
        assert first["ranger_mutations"] == 3
        assert len(transport.writes) == 3
        rows = controller.repository.list_projections(version.id)
        assert {row.sync_status for row in rows} == {"SYNCHRONIZED"}
        assert all(row.observed_checksum == row.desired_checksum for row in rows)

        session.rollback()
        second = controller.reconcile(policy_version_id=str(version.id))
        session.commit()
        assert second["status"] == "SYNCHRONIZED"
        assert second["ranger_mutations"] == 0
        assert {item["action"] for item in second["projections"]} == {"NO_CHANGE"}
        assert len(transport.writes) == 3
    finally:
        client.close()


def test_unmanaged_same_name_is_untouched(session) -> None:
    transport = RangerTransport()
    client = ranger(transport)
    try:
        version = activate(
            session,
            client,
            key="sales.customer.unmanaged",
            policy=logical_policy(phone_mask=False),
        )
        controller = PolicyReconciliationService(session, settings(), ranger_client=client)
        logical = controller.compiler.compile(
            policy_key=version.policy_key,
            version=version.version,
            logical_policy=LogicalDataAccessPolicy.model_validate(version.logical_policy),
        )
        access = next(item for item in logical if item.projection_type == "ACCESS")
        unmanaged = deepcopy(access.document)
        unmanaged["id"] = 55
        unmanaged["description"] = "manually managed Ranger policy"
        transport.policies[access.ranger_policy_name] = unmanaged

        with pytest.raises(ExternalSystemError, match="unmanaged"):
            controller.reconcile(policy_version_id=str(version.id))
        session.commit()
        assert transport.writes == []
        row = next(
            row
            for row in controller.repository.list_projections(version.id)
            if row.ranger_policy_name == access.ranger_policy_name
        )
        assert row.sync_status == "UNMANAGED_CONFLICT"
    finally:
        client.close()


def test_post_write_mismatch_is_not_synchronized(session) -> None:
    transport = RangerTransport()
    transport.force_post_read_mismatch = True
    client = ranger(transport)
    try:
        version = activate(
            session,
            client,
            key="sales.customer.mismatch",
            policy={
                **logical_policy(phone_mask=False),
                "row_filter": None,
            },
        )
        controller = PolicyReconciliationService(session, settings(), ranger_client=client)
        with pytest.raises(ExternalSystemError, match="read-back mismatch"):
            controller.reconcile(policy_version_id=str(version.id))
        session.commit()
        row = controller.repository.list_projections(version.id)[0]
        assert row.sync_status == "MISMATCH"
        assert row.reconciliation_details["semantic_convergence"] is False
    finally:
        client.close()


def test_stale_v1_target_after_v2_activation_has_zero_ranger_mutation(session) -> None:
    transport = RangerTransport()
    client = ranger(transport)
    try:
        v1 = activate(
            session,
            client,
            key="sales.customer.fence",
            policy=logical_policy(phone_mask=False),
        )
        service = DataAccessPolicyService(session, settings(), ranger_client=client)
        with session.begin():
            v2 = service.create_version(
                policy_key=v1.policy_key,
                logical_policy={
                    **logical_policy(phone_mask=False),
                    "access": {"select": "ALLOW"},
                },
                actor_id="admin",
                actor_name="Admin",
            )
        with session.begin():
            target = service.read_activation_target(
                policy_key=v1.policy_key,
                version=v2.version,
            )
        validation = service.validate_activation_subjects(target)
        with session.begin():
            service.activate_version(
                validation=validation,
                actor_id="admin",
                actor_name="Admin",
            )

        controller = PolicyReconciliationService(session, settings(), ranger_client=client)
        result = controller.reconcile(policy_version_id=str(v1.id))
        session.commit()
        assert result["status"] == "SUPERSEDED"
        assert result["ranger_mutations"] == 0
        assert transport.writes == []
    finally:
        client.close()


def test_rollback_reactivates_immutable_version_and_reuses_normal_reconciliation(session) -> None:
    transport = RangerTransport()
    client = ranger(transport)
    try:
        v1 = activate(
            session,
            client,
            key="sales.customer.rollback",
            policy=logical_policy(),
        )
        v1_payload = deepcopy(v1.logical_policy)
        controller = PolicyReconciliationService(session, settings(), ranger_client=client)
        first = controller.reconcile(policy_version_id=str(v1.id))
        session.commit()
        assert first["status"] == "SYNCHRONIZED"

        service = DataAccessPolicyService(session, settings(), ranger_client=client)
        with session.begin():
            v2 = service.create_version(
                policy_key=v1.policy_key,
                logical_policy={
                    **logical_policy(phone_mask=False),
                    "access": {"select": "ALLOW"},
                    "row_filter": None,
                },
                actor_id="admin",
                actor_name="Admin",
            )
        with session.begin():
            target = service.read_activation_target(
                policy_key=v1.policy_key,
                version=v2.version,
            )
        validation = service.validate_activation_subjects(target)
        with session.begin():
            service.activate_version(
                validation=validation,
                actor_id="admin",
                actor_name="Admin",
            )
        second = controller.reconcile(policy_version_id=str(v2.id))
        session.commit()
        assert second["status"] == "SYNCHRONIZED"

        with session.begin():
            rollback_target = service.read_rollback_target(
                policy_key=v1.policy_key,
                target_version=1,
            )
        rollback_validation = service.validate_activation_subjects(rollback_target)
        with session.begin():
            rolled_back, changed = service.rollback(
                policy_key=v1.policy_key,
                target_version=1,
                actor_id="admin",
                actor_name="Admin",
                validation=rollback_validation,
            )
        assert changed is True
        assert rolled_back.id == v1.id
        assert rolled_back.logical_policy == v1_payload

        rollback_result = controller.reconcile(policy_version_id=str(v1.id))
        session.commit()
        assert rollback_result["status"] == "SYNCHRONIZED"
        assert controller.repository.get_active(v1.policy_key).id == v1.id
        assert v1.logical_policy == v1_payload
    finally:
        client.close()
