from __future__ import annotations

import json

import httpx

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger import RangerClient
from app.models.enums import ReconciliationAction


def test_openmetadata_column_update_uses_targeted_fqn_api_and_reads_back() -> None:
    calls: list[tuple[str, str, object]] = []
    get_column_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_column_count
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/v1/tables/name/hive.sales.customers"):
            return httpx.Response(200, json={"id": "table-id", "tags": []})
        if "/v1/columns/name/" in request.url.path and request.method == "GET":
            get_column_count += 1
            tags = (
                []
                if get_column_count == 1
                else [
                    {
                        "tagFQN": "PII.Email",
                        "source": "Classification",
                        "labelType": "Automated",
                        "state": "Confirmed",
                    }
                ]
            )
            return httpx.Response(200, json={"name": "email", "tags": tags})
        if request.method == "PUT":
            return httpx.Response(200, json={"name": "email"})
        return httpx.Response(500)

    client = OpenMetadataClient(base_url="http://openmetadata/api", token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://openmetadata/api",
        transport=httpx.MockTransport(handler),
    )

    observed = client.apply_confirmed_tags(
        entity_type="table",
        entity_fqn="hive.sales.customers",
        entity_tags=[],
        field_tags={"columns.email": ["PII.Email"]},
    )
    client.assert_confirmed_tags(
        observed,
        entity_tags=[],
        field_tags={"columns.email": ["PII.Email"]},
    )

    put = next(body for method, _path, body in calls if method == "PUT")
    assert put["tags"][0]["tagFQN"] == "PII.Email"
    assert put["tags"][0]["state"] == "Confirmed"
    assert any(
        "/v1/columns/name/" in path
        for method, path, _body in calls
        if method == "PUT"
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


