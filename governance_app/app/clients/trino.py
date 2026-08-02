from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Protocol

import trino

from app.core.errors import ConfigurationError, ExternalSystemError


@dataclass(frozen=True, slots=True)
class QueryObservation:
    allowed: bool
    duration_ms: float
    error_class: str | None = None
    error_message: str | None = None


class TrinoExecutor(Protocol):
    def execute(self, *, identity: str, sql: str) -> QueryObservation: ...


class TrinoDBAPIExecutor:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        catalog: str,
        schema: str,
        http_scheme: str,
        timeout_seconds: float,
    ) -> None:
        self.host = host
        self.port = port
        self.catalog = catalog
        self.schema = schema
        self.http_scheme = http_scheme
        self.timeout_seconds = timeout_seconds

    def execute(self, *, identity: str, sql: str) -> QueryObservation:
        started = time.perf_counter()
        connection = trino.dbapi.connect(
            host=self.host,
            port=self.port,
            user=identity,
            catalog=self.catalog,
            schema=self.schema,
            http_scheme=self.http_scheme,
            request_timeout=self.timeout_seconds,
        )
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            cursor.fetchmany(1)
            return QueryObservation(allowed=True, duration_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:  # library exception hierarchy varies by version
            message = str(exc)[:1000]
            lowered = message.lower()
            duration = (time.perf_counter() - started) * 1000
            if "access denied" in lowered or "permission denied" in lowered or "not authorized" in lowered:
                return QueryObservation(
                    allowed=False,
                    duration_ms=duration,
                    error_class=exc.__class__.__name__,
                    error_message=message,
                )
            raise ExternalSystemError(
                f"Trino verification failed: {message}",
                system="trino",
                retryable=any(token in lowered for token in ("timeout", "temporar", "unavailable", "502", "503")),
            ) from exc
        finally:
            connection.close()


def query_fingerprint(sql: str) -> str:
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()
