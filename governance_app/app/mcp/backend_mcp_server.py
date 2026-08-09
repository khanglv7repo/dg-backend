"""FastMCP Server exposing read-only governance diagnostics and policy management to AI Agent.

Per context architecture (05-ai-agent-and-mcp.md), FastMCP runs on the internal
Docker network and exposes controlled, bounded capabilities for:
- Ranger state inspection (`inspect_ranger_state`)
- Bounded read-only Trino diagnostics (`query_trino_readonly`)
- Governance policy inspection (`inspect_policy_state`)
- Submitting approved policy commands (`submit_policy_command`)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import get_settings

logger = logging.getLogger(__name__)

mcp = FastMCP("GovernanceBackendMCP")


@mcp.tool()
def inspect_ranger_state(service_name: str | None = None) -> dict[str, Any]:
    """Controlled read-only inspection of Ranger policies and tag definitions.

    Does not require or expose direct Ranger credentials to the Agent.
    """
    settings = get_settings()
    target_service = service_name or settings.ranger_service_name
    result: dict[str, Any] = {
        "service_name": target_service,
        "tag_service_name": settings.ranger_tag_service_name,
    }

    try:
        ranger_client = RangerClient(
            base_url=settings.ranger_base_url,
            service_name=target_service,
        )
        policies = ranger_client.get_policies()
        result["policies_count"] = len(policies)
        result["policies"] = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "isEnabled": p.get("isEnabled"),
                "resources": p.get("resources"),
            }
            for p in policies[:20]
        ]
    except Exception as exc:
        logger.warning("Failed to fetch Ranger policies: %s", exc)
        result["policies_error"] = str(exc)

    try:
        tag_client = RangerTagStoreClient(
            base_url=settings.ranger_tag_store_base_url,
            tag_service_name=settings.ranger_tag_service_name,
        )
        tag_defs = tag_client.get_tag_definitions()
        result["tag_definitions"] = [t.get("name") for t in tag_defs]
    except Exception as exc:
        logger.warning("Failed to fetch Ranger tag definitions: %s", exc)
        result["tag_defs_error"] = str(exc)

    return result


@mcp.tool()
def query_trino_readonly(
    query: str,
    username: str = "alice",
    limit: int = 50,
) -> dict[str, Any]:
    """Execute a single read-only SQL query against Trino under a specified runtime persona.

    Bounded to max rows and SELECT queries only. Used for diagnostic inspection,
    NOT mandatory workflow verification.
    """
    cleaned = query.strip().rstrip(";").lower()
    if not (cleaned.startswith("select") or cleaned.startswith("show") or cleaned.startswith("describe") or cleaned.startswith("explain")):
        return {
            "error": "Only read-only queries (SELECT, SHOW, DESCRIBE, EXPLAIN) are permitted."
        }

    trino_host = os.getenv("TRINO_HOST", "trino")
    trino_port = os.getenv("TRINO_PORT", "8080")
    trino_url = f"http://{trino_host}:{trino_port}/v1/statement"

    bounded_limit = min(max(1, limit), 100)
    effective_query = query if "limit" in cleaned else f"{query} LIMIT {bounded_limit}"

    headers = {
        "X-Trino-User": username,
        "X-Trino-Catalog": "hive",
        "X-Trino-Schema": "default",
        "Content-Type": "text/plain",
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(trino_url, content=effective_query, headers=headers)
            response.raise_for_status()
            data = response.json()

            # Poll nextUri if results are pending
            next_uri = data.get("nextUri")
            columns = data.get("columns", [])
            rows = data.get("data", [])

            attempts = 0
            while next_uri and attempts < 10:
                attempts += 1
                r = client.get(next_uri)
                r.raise_for_status()
                data = r.json()
                next_uri = data.get("nextUri")
                if "columns" in data and not columns:
                    columns = data["columns"]
                if "data" in data:
                    rows.extend(data["data"])

            return {
                "persona": username,
                "columns": [c.get("name") for c in (columns or [])],
                "rows": rows[:bounded_limit],
                "row_count": len(rows[:bounded_limit]),
            }
    except Exception as exc:
        logger.warning("Trino query failed for persona %s: %s", username, exc)
        return {
            "persona": username,
            "error": str(exc),
        }


@mcp.tool()
def inspect_policy_state() -> dict[str, Any]:
    """Inspect active data-access policy state in the governance control plane."""
    # Placeholder for reading active policy versions from DB
    return {
        "status": "ok",
        "active_policies": [],
    }


@mcp.tool()
def submit_policy_command(command_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit an approved data-access policy proposal command for backend compilation and Ranger sync."""
    return {
        "command_id": command_id,
        "status": "accepted",
        "message": "Policy proposal command received for processing.",
    }


if __name__ == "__main__":
    mcp.run()
