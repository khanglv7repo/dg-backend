from __future__ import annotations

import json

import httpx
import pytest

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger import RangerClient
from app.core.errors import NotFoundError, ValidationError
from app.models.enums import ReconciliationAction


def test_openmetadata_column_update_uses_parent_table_fqn_patch_and_reads_back() -> None:
    calls: list[tuple[str, str, object]] = []
    column_read_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal column_read_count

        body = (
            json.loads(request.content)
            if request.content
            else None
        )
        calls.append(
            (
                request.method,
                request.url.path,
                body,
            )
        )

        if request.url.path.endswith(
            "/v1/tables/name/hive.sales.customers"
        ):
            fields = request.url.params.get("fields")

            if fields == "tags,columns":
                column_read_count += 1

                tags = (
                    []
                    if column_read_count == 1
                    else [
                        {
                            "tagFQN": "PII.Email",
                            "source": "Classification",
                            "labelType": "Automated",
                            "state": "Confirmed",
                        }
                    ]
                )

                return httpx.Response(
                    200,
                    json={
                        "id": "table-id",
                        "name": "customers",
                        "columns": [
                            {
                                "name": "email",
                                "fullyQualifiedName": (
                                    "hive.sales.customers.email"
                                ),
                                "tags": tags,
                            }
                        ],
                    },
                )

            if fields == "tags":
                return httpx.Response(
                    200,
                    json={
                        "id": "table-id",
                        "name": "customers",
                        "tags": [],
                    },
                )

        if (
            request.method == "PATCH"
            and request.url.path.endswith(
                "/v1/tables/name/hive.sales.customers"
            )
        ):
            return httpx.Response(
                200,
                json={"id": "table-id"},
            )

        return httpx.Response(500)

    client = OpenMetadataClient(
        base_url="http://openmetadata/api",
        token=None,
    )
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    observed = client.apply_confirmed_tags(
        entity_type="table",
        entity_fqn="hive.sales.customers",
        entity_tags=[],
        field_tags={
            "columns.email": ["PII.Email"]
        },
    )

    client.assert_confirmed_tags(
        observed,
        entity_tags=[],
        field_tags={
            "columns.email": ["PII.Email"]
        },
    )

    patch = next(
        body
        for method, path, body in calls
        if method == "PATCH"
        and path.endswith("/v1/tables/name/hive.sales.customers")
    )

    assert patch == [
        {
            "op": "replace",
            "path": "/columns/0/tags",
            "value": [
                {
                    "tagFQN": "PII.Email",
                    "source": "Classification",
                    "labelType": "Automated",
                    "state": "Confirmed",
                }
            ],
        }
    ]

    assert not any(
        "/v1/columns/name/" in path
        for _method, path, _body in calls
    )


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


def test_entity_suggested_tag_is_promoted_to_confirmed() -> None:
    calls: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/v1/tables/name/hive.sales.customers"):
            return httpx.Response(
                200,
                json={
                    "id": "table-id",
                    "tags": [
                        {"tagFQN": "PII.Phone", "state": "Suggested"},
                        {"tagFQN": "Lifecycle.Pending", "state": "Suggested"},
                    ],
                },
            )
        if request.method == "PATCH":
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "table-id"})
        return httpx.Response(500)

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    client._merge_entity_tags(
        entity_type="table",
        entity_fqn="hive.sales.customers",
        tags=["PII.Phone"],
        label_type="Automated",
    )

    assert len(calls) == 2
    assert calls[0][0]["value"] == [
        {"tagFQN": "Lifecycle.Pending", "state": "Suggested"},
    ]
    value = calls[1][0]["value"]
    assert value == [
        {"tagFQN": "Lifecycle.Pending", "state": "Suggested"},
        {
            "tagFQN": "PII.Phone",
            "state": "Confirmed",
            "source": "Classification",
            "labelType": "Automated",
        },
    ]


def test_column_suggested_tag_is_promoted_to_confirmed() -> None:
    calls: list[list[dict]] = []
    read_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal read_count
        if request.method == "GET" and request.url.path.endswith("/v1/tables/name/hive.sales.customers"):
            read_count += 1
            return httpx.Response(
                200,
                json={
                    "id": "table-id",
                    "columns": [
                        {
                            "name": "phone",
                            "fullyQualifiedName": "hive.sales.customers.phone",
                            "tags": [
                                {"tagFQN": "PII.Phone", "state": "Suggested"},
                                {"tagFQN": "PII.Email", "state": "Suggested"},
                            ],
                        }
                    ],
                },
            )
        if request.method == "PATCH":
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "table-id"})
        return httpx.Response(500)

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    client._merge_column_tags(
        column_fqn="hive.sales.customers.phone",
        entity_type="table",
        tags=["PII.Phone"],
        label_type="Automated",
    )

    assert len(calls) == 2
    assert calls[0][0]["value"] == [
        {"tagFQN": "PII.Email", "state": "Suggested"},
    ]
    value = calls[1][0]["value"]
    assert value == [
        {"tagFQN": "PII.Email", "state": "Suggested"},
        {
            "tagFQN": "PII.Phone",
            "state": "Confirmed",
            "source": "Classification",
            "labelType": "Automated",
        },
    ]
    assert read_count == 2


def test_confirmed_tag_is_not_duplicated() -> None:
    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)

    merged = client._merge_confirmed_tag_labels(
        [
            {"tagFQN": "PII.Phone", "state": "Confirmed", "labelType": "Manual"},
        ],
        desired_tags=["PII.Phone"],
        label_type="Automated",
    )

    assert [item["tagFQN"] for item in merged] == ["PII.Phone"]
    assert merged[0]["state"] == "Confirmed"


def test_unrelated_suggested_tag_is_preserved_but_not_authoritative() -> None:
    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)

    labels = client._merge_confirmed_tag_labels(
        [{"tagFQN": "PII.Email", "state": "Suggested"}],
        desired_tags=["PII.Phone"],
        label_type="Automated",
    )

    assert labels == [
        {"tagFQN": "PII.Email", "state": "Suggested"},
        {
            "tagFQN": "PII.Phone",
            "source": "Classification",
            "labelType": "Automated",
            "state": "Confirmed",
        },
    ]
    assert client._confirmed_tag_fqns(labels) == ["PII.Phone"]


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


def test_openmetadata_native_tag_suggestion_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "suggestion-id", "status": "Open"})

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )
    response = client.create_tag_suggestion(
        entity_type="table",
        entity_fqn="hive.sales.customers",
        field_path="columns.email",
        tags=["PII.Email"],
        description="Rule evidence",
        label_type="Automated",
    )

    assert response["id"] == "suggestion-id"
    assert captured["type"] == "SuggestTagLabel"
    assert captured["entityLink"] == "<#E::table::hive.sales.customers::columns::email>"
    assert captured["tagLabels"][0] == {
        "tagFQN": "PII.Email",
        "source": "Classification",
        "labelType": "Automated",
        "state": "Suggested",
    }


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
