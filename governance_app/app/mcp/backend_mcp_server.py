"""FastMCP Server exposing read-only governance diagnostics to AI Agent.

Per context architecture (05-ai-agent-and-mcp.md), FastMCP runs on the internal
Docker network and exposes controlled, bounded capabilities for:
- Ranger state inspection (`inspect_ranger_state`)
- Bounded read-only Trino diagnostics (`query_trino_readonly`)
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.config import get_settings
from app.services.ranger_inspection import RangerInspectionService
from app.services.trino_diagnostic import TrinoDiagnosticService

logger = logging.getLogger(__name__)

mcp = FastMCP("GovernanceBackendMCP")


@mcp.tool()
def inspect_ranger_state(service_name: str | None = None) -> dict[str, Any]:
    """Controlled read-only inspection of Ranger policies and tag definitions.

    Uses RangerInspectionService to call live Ranger endpoints using configured credentials.
    Raises exception on connection/authentication failure (does not mask errors).
    """
    settings = get_settings()
    service = RangerInspectionService(settings)
    return service.inspect(service_name=service_name)


@mcp.tool()
def query_trino_readonly(
    query: str,
    username: str = "alice",
    limit: int = 50,
) -> dict[str, Any]:
    """Execute a single validated read-only SQL query against Trino under a specified runtime persona.

    Bounded to max 100 rows and SELECT/SHOW/DESCRIBE/EXPLAIN queries only.
    Rejects multi-statement and DDL/DML queries.
    """
    service = TrinoDiagnosticService()
    return service.execute_diagnostic(query=query, username=username, limit=limit)


if __name__ == "__main__":
    mcp.run()
