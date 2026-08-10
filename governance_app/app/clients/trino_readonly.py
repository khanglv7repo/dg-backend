from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.errors import ConfigurationError, ExternalSystemError, ValidationError

_ALLOWED_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH")
_FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CALL",
        "EXECUTE",
        "GRANT",
        "REVOKE",
        "SET",
        "RESET",
        "USE",
        "PREPARE",
        "DEALLOCATE",
        "START",
        "COMMIT",
        "ROLLBACK",
        "ANALYZE",
        "REFRESH",
    }
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate_readonly_sql(sql: str) -> str:
    """Conservatively accept one read-only Trino statement.

    This is deliberately a small lab guard, not a SQL parser. The configured
    read-only Trino identity remains the primary runtime safety boundary.
    """

    statement = str(sql).strip()
    if not statement:
        raise ValidationError("SQL must not be empty")
    if "\x00" in statement:
        raise ValidationError("SQL contains an invalid NUL byte")

    # Permit exactly one optional trailing semicolon. Reject every other
    # semicolon conservatively, including semicolons inside strings/comments.
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if ";" in statement:
        raise ValidationError("exactly one SQL statement is allowed")
    if not statement:
        raise ValidationError("SQL must not be empty")

    tokens = [token.upper() for token in _TOKEN_RE.findall(statement)]
    if not tokens or tokens[0] not in _ALLOWED_PREFIXES:
        raise ValidationError(
            "only SELECT, SHOW, DESCRIBE, EXPLAIN, or bounded WITH read queries are allowed"
        )

    forbidden = sorted(set(tokens) & _FORBIDDEN_KEYWORDS)
    if forbidden:
        raise ValidationError(
            "read-only Trino query rejected forbidden SQL keyword(s): "
            + ", ".join(forbidden)
        )

    if tokens[0] == "WITH" and "SELECT" not in tokens:
        raise ValidationError("WITH is allowed only when it resolves to a SELECT query")

    if tokens[0] == "EXPLAIN":
        explained = tokens[1] if len(tokens) > 1 else ""
        if explained not in {"SELECT", "SHOW", "DESCRIBE", "DESC", "WITH"}:
            raise ValidationError("EXPLAIN is allowed only for a read query")

    return statement


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time, Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


class TrinoReadonlyClient:
    """Bounded diagnostic Trino DBAPI client using a configured read-only persona."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.trino_readonly_enabled:
            raise ConfigurationError("read-only Trino diagnostics are disabled")
        if not settings.trino_readonly_user:
            raise ConfigurationError("TRINO_READONLY_USER is required")

    def query(self, sql: str) -> dict[str, Any]:
        statement = validate_readonly_sql(sql)
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(statement)
            description = cursor.description or []
            columns = [str(item[0]) for item in description]
            if len(columns) > self.settings.trino_readonly_max_columns:
                raise ValidationError(
                    "Trino result exceeds configured maximum columns",
                    details={
                        "max_columns": self.settings.trino_readonly_max_columns,
                        "observed_columns": len(columns),
                    },
                )

            raw_rows = cursor.fetchmany(self.settings.trino_readonly_max_rows + 1)
            truncated = len(raw_rows) > self.settings.trino_readonly_max_rows
            rows = [
                [_json_value(value) for value in row]
                for row in raw_rows[: self.settings.trino_readonly_max_rows]
            ]
            result: dict[str, Any] = {
                "columns": columns,
                "rows": rows,
                "row_count_returned": len(rows),
                "truncated": truncated,
                "query_id": getattr(cursor, "query_id", None),
            }
            encoded = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
            if len(encoded) > self.settings.trino_readonly_max_response_bytes:
                raise ValidationError(
                    "Trino diagnostic response exceeds configured size limit",
                    details={
                        "max_response_bytes": self.settings.trino_readonly_max_response_bytes,
                    },
                )
            return result
        except (ValidationError, ConfigurationError):
            raise
        except Exception as exc:
            retryable = self._retryable_exception(exc)
            raise ExternalSystemError(
                "Trino read-only diagnostic query failed",
                system="trino",
                retryable=retryable,
            ) from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            try:
                connection.close()
            except Exception:
                pass


    @staticmethod
    def _retryable_exception(exc: Exception) -> bool:
        try:
            from trino.exceptions import (
                HttpError,
                TrinoConnectionError,
                TrinoExternalError,
                TrinoInternalError,
            )
        except ImportError:
            return False
        return isinstance(
            exc,
            (HttpError, TrinoConnectionError, TrinoExternalError, TrinoInternalError),
        )

    def _connect(self):
        try:
            from trino.auth import BasicAuthentication
            from trino.dbapi import connect
        except ImportError as exc:
            raise ConfigurationError(
                "Trino Python client is not installed; install the project 'trino' extra"
            ) from exc

        auth = None
        if self.settings.trino_readonly_password is not None:
            if self.settings.trino_readonly_http_scheme != "https":
                raise ConfigurationError(
                    "Trino password authentication requires TRINO_READONLY_HTTP_SCHEME=https"
                )
            auth = BasicAuthentication(
                self.settings.trino_readonly_user or "",
                self.settings.trino_readonly_password.get_secret_value(),
            )

        return connect(
            host=self.settings.trino_readonly_host,
            port=self.settings.trino_readonly_port,
            user=self.settings.trino_readonly_user,
            catalog=self.settings.trino_readonly_catalog,
            schema=self.settings.trino_readonly_schema,
            http_scheme=self.settings.trino_readonly_http_scheme,
            auth=auth,
            request_timeout=self.settings.trino_readonly_timeout_seconds,
        )
