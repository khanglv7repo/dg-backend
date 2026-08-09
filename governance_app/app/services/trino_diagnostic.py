"""Bounded read-only Trino diagnostic service.

Executes a single read-only SQL diagnostic statement under a supplied runtime persona.
Guarantees single-statement execution, read-only SQL verb whitelist, DDL/DML rejection,
and row bounding without rewriting non-SELECT statements.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

READ_ONLY_VERBS = frozenset({"SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH"})

DISALLOWED_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "GRANT",
        "REVOKE",
        "TRUNCATE",
        "EXECUTE",
        "MERGE",
        "CALL",
    }
)


def validate_read_only_query(query: str) -> tuple[str, str]:
    """Validate that query is a single read-only SQL statement.

    Returns (cleaned_query, verb).
    Raises ValidationError if statement is invalid, multi-statement, or non-read-only.
    """
    raw = query.strip()
    if not raw:
        raise ValidationError("Query string cannot be empty", details={"field": "query"})

    # Check for multiple statements separated by semicolon
    # Ignore trailing semicolon at the very end
    trimmed = raw.rstrip(";").strip()
    if ";" in trimmed:
        raise ValidationError(
            "Multi-statement queries separated by semicolon are rejected",
            details={"field": "query"},
        )

    # Extract leading verb
    tokens = re.findall(r"\b[A-Za-z_]+\b", trimmed)
    if not tokens:
        raise ValidationError("Could not parse SQL query verb", details={"field": "query"})

    first_verb = tokens[0].upper()
    if first_verb not in READ_ONLY_VERBS:
        raise ValidationError(
            f"Statement verb {first_verb!r} is not allowed for diagnostic query. "
            f"Allowed verbs: {sorted(READ_ONLY_VERBS)}",
            details={"field": "query"},
        )

    # Check for disallowed DDL/DML keywords in upper-cased tokens
    upper_tokens = set(t.upper() for t in tokens)
    disallowed_found = upper_tokens.intersection(DISALLOWED_KEYWORDS)
    if disallowed_found:
        raise ValidationError(
            f"Disallowed DDL/DML keywords found in query: {sorted(disallowed_found)}",
            details={"field": "query"},
        )

    return trimmed, first_verb


class TrinoDiagnosticService:
    """Service for running bounded read-only Trino diagnostic queries."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | str | None = None,
        catalog: str | None = None,
        schema: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.host = host or os.getenv("TRINO_HOST", "trino")
        self.port = str(port or os.getenv("TRINO_PORT", "8080"))
        self.catalog = catalog or os.getenv("TRINO_CATALOG", "hive")
        self.schema = schema or os.getenv("TRINO_SCHEMA", "default")
        self.timeout = timeout
        self.base_url = f"http://{self.host}:{self.port}"

    def execute_diagnostic(
        self,
        query: str,
        *,
        username: str = "alice",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Execute a validated single read-only diagnostic statement."""
        cleaned_query, verb = validate_read_only_query(query)

        bounded_limit = min(max(1, limit), 100)

        # Apply LIMIT only to SELECT or WITH statements when not already limited
        if verb in ("SELECT", "WITH"):
            if not re.search(r"\blimit\b\s+\d+", cleaned_query, re.IGNORECASE):
                effective_query = f"{cleaned_query} LIMIT {bounded_limit}"
            else:
                effective_query = cleaned_query
        else:
            # SHOW, DESCRIBE, EXPLAIN are not rewritten with LIMIT
            effective_query = cleaned_query

        headers = {
            "X-Trino-User": username,
            "X-Trino-Catalog": self.catalog,
            "X-Trino-Schema": self.schema,
            "Content-Type": "text/plain",
        }

        statement_url = f"{self.base_url}/v1/statement"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(statement_url, content=effective_query, headers=headers)
            response.raise_for_status()
            data = response.json()

            next_uri = data.get("nextUri")
            columns = data.get("columns", [])
            rows = data.get("data", [])

            attempts = 0
            while next_uri and attempts < 15:
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
                "query": effective_query,
                "columns": [c.get("name") for c in (columns or [])],
                "rows": rows[:bounded_limit],
                "row_count": len(rows[:bounded_limit]),
            }
