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

            if fields == "columns":
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

