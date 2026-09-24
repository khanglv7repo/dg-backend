from __future__ import annotations

import json

import httpx
import pytest

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger import RangerClient
from app.core.errors import NotFoundError, ValidationError
from app.models.enums import ReconciliationAction


def test_openmetadata_confirmed_tag_snapshot_uses_live_entity_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/tables/name/hive.sales.customers"):
            return httpx.Response(
                200,
                json={
                    "id": "table-id",
                    "tags": [
                        {
                            "tagFQN": "Sensitivity.Confidential",
                            "state": "Confirmed",
                        },
                        {
                            "tagFQN": "Lifecycle.Pending",
                            "state": "Suggested",
                        },
                    ],
                    "columns": [
                        {
                            "name": "email",
                            "tags": [
                                {
                                    "tagFQN": "PII.Email",
                                    "state": "Confirmed",
                                }
                            ],
                        },
                        {
                            "name": "mobile_phone",
                            "tags": [
                                {
                                    "tagFQN": "PII.Phone",
                                    "state": "Suggested",
                                }
                            ],
                        },
                        {"name": "customer_id", "tags": []},
                    ],
                },
            )
        return httpx.Response(404)

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    snapshot = client.get_confirmed_tag_snapshot(
        entity_type="table",
        entity_fqn="hive.sales.customers",
    )

    assert snapshot == {
        "entity_tags": ["Sensitivity.Confidential"],
        "field_tags": {"columns.email": ["PII.Email"]},
        "tags": ["PII.Email", "Sensitivity.Confidential"],
        "field_paths": {"PII.Email": ["columns.email"]},
        "all_field_paths": [
            "columns.customer_id",
            "columns.email",
            "columns.mobile_phone",
        ],
    }


def test_openmetadata_confirmed_snapshot_excludes_explicit_suggested_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/tables/name/hive.sales.customers"):
            return httpx.Response(
                200,
                json={
                    "id": "table-id",
                    "columns": [
                        {
                            "name": "phone",
                            "tags": [
                                {"tagFQN": "PII.Phone", "state": "Confirmed"},
                                {"tagFQN": "PII.Email", "state": "Suggested"},
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    snapshot = client.get_confirmed_tag_snapshot(
        entity_type="table",
        entity_fqn="hive.sales.customers",
    )

    assert snapshot["field_tags"] == {"columns.phone": ["PII.Phone"]}
    assert snapshot["tags"] == ["PII.Phone"]


def test_openmetadata_confirmed_snapshot_accepts_missing_state_as_confirmed() -> None:
    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)

    assert client._confirmed_tag_fqns(
        [
            {"tagFQN": "PII.Phone"},
            {"tagFQN": "PII.Email", "state": "Suggested"},
        ]
    ) == ["PII.Phone"]


def test_list_confirmed_table_tag_snapshots_follows_openmetadata_paging() -> None:
    seen_after: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        seen_after.append(after)
        if after is None:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "name": "customers",
                            "fullyQualifiedName": "svc.db.crm.customers",
                            "columns": [
                                {
                                    "name": "phone",
                                    "tags": [{"tagFQN": "PII.Phone", "state": "Confirmed"}],
                                }
                            ],
                        }
                    ],
                    "paging": {"after": "cursor-2", "total": 2},
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "contacts",
                        "fullyQualifiedName": "svc.db.crm.contacts",
                        "columns": [
                            {
                                "name": "email",
                                "tags": [{"tagFQN": "PII.Email", "state": "Confirmed"}],
                            }
                        ],
                    }
                ],
                "paging": {"total": 2},
            },
        )

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    snapshots = client.list_confirmed_table_tag_snapshots(limit=1)

    assert seen_after == [None, "cursor-2"]
    assert [item["entity_fqn"] for item in snapshots] == [
        "svc.db.crm.customers",
        "svc.db.crm.contacts",
    ]


def test_openmetadata_tag_validation_lists_all_missing_taxonomy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/tags/name/PII.Email"):
            return httpx.Response(200, json={"name": "Email"})
        return httpx.Response(404, json={"message": "tag instance not found"})

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValidationError, match="Missing OpenMetadata tags") as exc:
        client.validate_tag_fqns(["PII.Email", "PII.Address", "PII.Address"])

    assert str(exc.value) == "Missing OpenMetadata tags:\n- PII.Address"


def test_openmetadata_404_preserves_server_message() -> None:
    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                404,
                json={"message": "tag instance for PII.Address not found"},
            )
        ),
    )

    with pytest.raises(NotFoundError) as exc:
        client.get_tag("PII.Address")

    assert str(exc.value) == (
        "OpenMetadata returned 404 for /v1/tags/name/PII.Address: "
        "tag instance for PII.Address not found"
    )
