from __future__ import annotations

from typing import Any

from app.clients.trino_readonly import TrinoReadonlyClient
from app.core.config import Settings


class TrinoReadonlyService:
    """Application facade for the MCP diagnostic-only Trino capability."""

    def __init__(self, settings: Settings) -> None:
        self.client = TrinoReadonlyClient(settings)

    def query(self, *, sql: str) -> dict[str, Any]:
        return self.client.query(sql)
