import json

import httpx
import pytest

from app.clients.mcp import OpenMetadataMCPClient


def test_mcp_client_blocks_mutation_tools_before_network_call() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    client = OpenMetadataMCPClient(endpoint="http://openmetadata/mcp", token="bot-token")
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="not allowed"):
        client.call_tool("patch_entity", {"fqn": "hive.sales.customers"})
    assert calls == 0


def test_mcp_client_uses_bearer_bot_token_and_read_tool() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"name": "customers"}},
        )

    client = OpenMetadataMCPClient(endpoint="http://openmetadata/mcp", token="agent-bot-token")
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.call_tool(
        "get_entity_details", {"entity_type": "table", "fqn": "hive.sales.customers"}
    )

    assert result == {"name": "customers"}
    assert captured["authorization"] == "Bearer agent-bot-token"
    assert captured["payload"]["params"]["name"] == "get_entity_details"
