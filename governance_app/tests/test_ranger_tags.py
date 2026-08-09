from __future__ import annotations

import json

import httpx

from app.clients.ranger_tags import RangerTagStoreClient


def test_tag_store_creates_resource_tag_and_mapping() -> None:
    requests = []
    tagdefs = []
    tags = []
    resources = []
    maps = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, str(request.url.query)))

        if request.method == "GET" and request.url.path.endswith("/tagdefs"):
            return httpx.Response(200, json=tagdefs)

        if request.method == "POST" and request.url.path.endswith("/tagdefs"):
            body = json.loads(request.content)
            value = {"id": 10, "guid": "tagdef-email", **body}
            tagdefs.append(value)
            return httpx.Response(201, json=value)

        if request.method == "GET" and "/tags/type/" in request.url.path:
            tag_type = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json=[item for item in tags if item["type"] == tag_type],
            )

        if request.method == "POST" and request.url.path.endswith("/tags"):
            body = json.loads(request.content)
            value = {
                "id": 20,
                "guid": "tag-email",
                **body,
            }
            tags.append(value)
            return httpx.Response(201, json=value)

        if request.method == "GET" and "/resources/service/" in request.url.path:
            return httpx.Response(200, json=resources)

        if request.method == "POST" and request.url.path.endswith("/resources"):
            body = json.loads(request.content)
            value = {
                "id": 30,
                "guid": "resource-email",
                **body,
            }
            resources.append(value)
            return httpx.Response(201, json=value)

        if (
            request.method == "GET"
            and request.url.path.endswith("/tagresourcemap/tag-resource-guid")
        ):
            return httpx.Response(404, json={"message": "not found"})

        if (
            request.method == "POST"
            and request.url.path.endswith("/tagresourcemaps")
        ):
            value = {
                "id": 40,
                "guid": "map-email",
                "resourceId": 30,
                "tagId": 20,
            }
            maps.append(value)
            return httpx.Response(201, json=value)

        if request.method == "GET" and request.url.path.endswith("/tags"):
            return httpx.Response(200, json=tags)

        if (
            request.method == "GET"
            and request.url.path.endswith("/tagresourcemaps")
        ):
            return httpx.Response(200, json=maps)

        return httpx.Response(
            500,
            json={"unexpected": str(request.url)},
        )

    client = RangerTagStoreClient(
        base_url="http://ranger/service/tags",
        username=None,
        password=None,
        resource_service_name="dev_trino",
        dry_run=False,
    )
    client.client.close()
    client.client = httpx.Client(
        base_url="http://ranger/service/tags",
        transport=httpx.MockTransport(handler),
    )

    result = client.reconcile_assignments(
        entity_fqn="hive.sales.customers",
        entity_tags=[],
        field_tags={"columns.email": ["PII.Email"]},
    )

    assert result["action"] == "SYNC"
    assert result["expected_assignments"] == [
        {
            "field_path": "columns.email",
            "tag": "PII.Email",
        }
    ]
    assert resources[0]["serviceName"] == "dev_trino"
    assert resources[0]["resourceElements"]["table"]["values"] == [
        "hive.sales.customers"
    ]
    assert resources[0]["resourceElements"]["column"]["values"] == ["email"]
    assert any(
        method == "POST" and path.endswith("/tagresourcemaps")
        for method, path, _query in requests
    )


def test_tag_store_does_not_treat_matching_unmanaged_resource_as_owned() -> None:
    requests = []
    resources = [
        {
            "id": 30,
            "guid": "managed-resource",
            "additionalInfo": {
                "managedBy": "dg-backend",
                "openmetadataFqn": "hive.sales.customers",
                "fieldPath": "columns.email",
            },
            "resourceElements": {
                "table": {"values": ["hive.sales.customers"]},
                "column": {"values": ["email"]},
            },
        },
        {
            "id": 31,
            "guid": "unmanaged-resource",
            "additionalInfo": {},
            "resourceElements": {
                "table": {"values": ["hive.sales.customers"]},
                "column": {"values": ["phone"]},
            },
        },
    ]
    tags = [
        {"id": 20, "guid": "tag-email", "type": "PII.Email"},
        {"id": 21, "guid": "tag-phone", "type": "PII.Phone"},
    ]
    maps = [
        {"id": 40, "resourceId": 30, "tagId": 20},
        {"id": 41, "resourceId": 31, "tagId": 21},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and "/resources/service/" in request.url.path:
            return httpx.Response(200, json=resources)
        if request.method == "GET" and request.url.path.endswith("/tags"):
            return httpx.Response(200, json=tags)
        if request.method == "GET" and request.url.path.endswith("/tagresourcemaps"):
            return httpx.Response(200, json=maps)
        if request.method == "DELETE" and request.url.path.endswith("/tagresourcemaps/40"):
            return httpx.Response(204)
        if request.method == "DELETE" and request.url.path.endswith("/tagresourcemaps/41"):
            return httpx.Response(500, json={"error": "must not delete unmanaged"})
        return httpx.Response(500, json={"unexpected": str(request.url)})

    client = RangerTagStoreClient(
        base_url="http://ranger/service/tags",
        username=None,
        password=None,
        resource_service_name="dev_trino",
        dry_run=False,
    )
    client.client.close()
    client.client = httpx.Client(
        base_url="http://ranger/service/tags",
        transport=httpx.MockTransport(handler),
    )

    removed = client.remove_stale_service_assignments(expected=set())

    assert removed == [
        {
            "entity_fqn": "hive.sales.customers",
            "field_path": "columns.email",
            "tag": "PII.Email",
            "map_id": "40",
        }
    ]
    assert ("DELETE", "/service/tags/tagresourcemaps/41") not in requests
