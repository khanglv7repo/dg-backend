from __future__ import annotations

import json

import anyio
from fastmcp import Client

from app.mcp.backend_mcp_server import mcp

EXPECTED_TOOLS = [
    "get_policy",
    "list_policy_versions",
    "preview_policy_change",
    "check_policy_conflict",
    "resolve_resource_mapping",
    "get_ranger_sync_status",
    "get_workflow_status",
    "get_audit_summary",
    "inspect_ranger_state",
    "query_trino_readonly",
    "create_policy_version",
    "activate_policy_version",
    "rollback_policy",
    "update_service_mapping",
    "request_ranger_sync",
]


def test_actual_fastmcp_protocol_lists_exact_r5_tools_and_json_schemas() -> None:
    async def run() -> None:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert [tool.name for tool in tools] == EXPECTED_TOOLS
            assert client.initialize_result is not None
            assert client.initialize_result.serverInfo.name
            for tool in tools:
                encoded = json.dumps(tool.inputSchema)
                assert encoded
                assert tool.inputSchema.get("type") == "object"

    anyio.run(run)
