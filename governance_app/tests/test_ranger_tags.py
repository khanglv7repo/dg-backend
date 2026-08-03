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
