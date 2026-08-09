from __future__ import annotations

from copy import deepcopy

import httpx
import pytest

from app.clients.ranger import RangerClient, canonical_hash, normalize_policy
from app.core.errors import ExternalSystemError


def client_with_handler(handler) -> RangerClient:
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
        transport=httpx.MockTransport(handler),
    )
    return client


def base_policy() -> dict:
    return {
        "isEnabled": True,
        "service": "dev_trino",
        "name": "dg-r4-sales-access",
        "policyType": 0,
        "policyPriority": 0,
        "description": "Data-access projection for sales [ACCESS]",
        "isAuditEnabled": True,
        "resources": {
            "catalog": {"values": ["dev"], "isExcludes": False, "isRecursive": False},
            "schema": {"values": ["sales"], "isExcludes": False, "isRecursive": False},
            "table": {"values": ["customer"], "isExcludes": False, "isRecursive": False},
        },
        "policyItems": [
            {
                "users": ["alice"],
                "accesses": [{"type": "select", "isAllowed": True}],
                "delegateAdmin": False,
            }
        ],
        "serviceType": "trino",
        "isDenyAllElse": False,
    }


def owned(policy: dict, *, policy_key: str, version: int = 1) -> dict:
    result = deepcopy(policy)
    desired_hash = canonical_hash(normalize_policy(policy) or {})
    result["description"] = (
        f"{policy['description']} | managed-by=dg-backend;policy-key={policy_key};"
        f"policy-version={version};projection-key=access;projection-type=ACCESS;"
        f"desired-sha256={desired_hash};"
    )
    result["id"] = 101
    result["guid"] = "guid-101"
    return result


def test_ranger_28_subject_lookup_paths_and_missing_behavior() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/users/userName/alice"):
            return httpx.Response(200, json={"id": 1, "name": "alice"})
        if request.url.path.endswith("/users/userName/missing"):
            return httpx.Response(404, json={"message": "not found"})
        if request.url.path.endswith("/groups/groupName/pii_readers"):
            return httpx.Response(200, json={"id": 2, "name": "pii_readers"})
        if request.url.path.endswith("/groups/groupName/missing"):
            return httpx.Response(404, json={"message": "not found"})
        raise AssertionError(request.url)

    client = client_with_handler(handler)
    try:
        assert client.user_exists("alice") is True
        assert client.user_exists("missing") is False
        assert client.group_exists("pii_readers") is True
        assert client.group_exists("missing") is False
    finally:
        client.close()

    assert paths == [
        "/service/xusers/users/userName/alice",
        "/service/xusers/users/userName/missing",
        "/service/xusers/groups/groupName/pii_readers",
        "/service/xusers/groups/groupName/missing",
    ]


def test_no_change_performs_no_ranger_write() -> None:
    desired = base_policy()
    current = owned(desired, policy_key="sales.customer")
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.method == "GET"
        return httpx.Response(200, json=current)

    client = client_with_handler(handler)
    try:
        result = client.reconcile_document(
            policy_key="sales.customer",
            document=desired,
            ownership={
                "policy-version": "1",
                "projection-type": "ACCESS",
                "projection-key": "access",
            },
        )
    finally:
        client.close()
    assert result["action"] == "NO_CHANGE"
    assert methods == ["GET"]


def test_same_semantics_new_version_updates_only_trace_marker() -> None:
    desired = base_policy()
    current = owned(desired, policy_key="sales.customer", version=1)
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=current)
        if request.method == "PUT":
            payload = request.read().decode()
            assert "policy-version=2" in payload
            return httpx.Response(200, json={**current, "version": 2})
        raise AssertionError(request.method)

    client = client_with_handler(handler)
    try:
        result = client.reconcile_document(
            policy_key="sales.customer",
            document=desired,
            ownership={
                "policy-version": "2",
                "projection-type": "ACCESS",
                "projection-key": "access",
            },
        )
    finally:
        client.close()
    assert result["action"] == "UPDATE"
    assert methods == ["GET", "PUT"]


@pytest.mark.parametrize(
    "description",
    [
        "manually-created policy",
        "Data-access | managed-by=dg-backend;policy-key=some-other-key;desired-sha256=x;",
    ],
)
def test_unmanaged_or_other_key_policy_is_never_mutated(description: str) -> None:
    desired = base_policy()
    current = {**desired, "id": 42, "description": description}
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=current)
        raise AssertionError("write must not be attempted")

    client = client_with_handler(handler)
    try:
        with pytest.raises(ExternalSystemError, match="not owned"):
            client.reconcile_document(
                policy_key="sales.customer",
                document=desired,
            )
    finally:
        client.close()
    assert methods == ["GET"]
