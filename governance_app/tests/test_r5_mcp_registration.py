from __future__ import annotations

import json

import anyio
from fastmcp import Client

from app.mcp.backend_mcp_server import mcp

R5_FROZEN_TOOLS = [
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

R6B_TOOLS = [*R5_FROZEN_TOOLS, "complete_classification_execution"]


def test_actual_fastmcp_protocol_preserves_r5_and_adds_r6b_completion_tool() -> None:
    async def run() -> None:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = [tool.name for tool in tools]
            assert names == R6B_TOOLS
            assert names[: len(R5_FROZEN_TOOLS)] == R5_FROZEN_TOOLS
            assert client.initialize_result is not None
            assert client.initialize_result.serverInfo.name
            for tool in tools:
                encoded = json.dumps(tool.inputSchema)
                assert encoded
                assert tool.inputSchema.get("type") == "object"

    anyio.run(run)
