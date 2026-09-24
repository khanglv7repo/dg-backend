from __future__ import annotations

import json

import anyio
from fastmcp import Client

from app.mcp.backend_mcp_server import mcp

BOUNDED_AGENT_TOOLS = [
    "get_policy",
    "list_policy_versions",
    "preview_policy_change",
    "check_policy_conflict",
    "resolve_resource_mapping",
    "get_ranger_sync_status",
    "get_audit_summary",
    "inspect_ranger_state",
    "query_trino_readonly",
    "create_policy_version",
    "get_tag_sync_observability",
]


def test_actual_fastmcp_protocol_exposes_only_bounded_agent_capabilities() -> None:
    async def run() -> None:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = [tool.name for tool in tools]
            assert names == BOUNDED_AGENT_TOOLS
            assert "activate_policy_version" not in names
            assert "rollback_policy" not in names
            assert "update_service_mapping" not in names
            assert "request_ranger_sync" not in names
            assert "complete_classification_execution" not in names
            assert client.initialize_result is not None
            assert client.initialize_result.serverInfo.name
            for tool in tools:
                encoded = json.dumps(tool.inputSchema)
                assert encoded
                assert tool.inputSchema.get("type") == "object"

    anyio.run(run)
