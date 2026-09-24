from __future__ import annotations

from typing import Any

import httpx

from app.core.errors import ExternalSystemError


class DQRunnerClient:
    """Narrow client for the internal metadata-ingestion DQ execution API."""

    def __init__(self, *, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self.client.close()

    def run_test_case(
        self,
        *,
        table_fqn: str,
        test_suite_fqn: str,
        test_case_name: str,
    ) -> dict[str, Any]:
        try:
            response = self.client.post(
                "/dq/run",
                json={
                    "table_fqn": table_fqn,
                    "test_suite_fqn": test_suite_fqn,
                    "test_case_name": test_case_name,
                },
            )
        except httpx.TimeoutException as exc:
            raise ExternalSystemError(
                "DQ runner timed out",
                system="dq-runner",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalSystemError(
                "DQ runner connection failed",
                system="dq-runner",
                retryable=True,
            ) from exc

        payload: dict[str, Any]
        try:
            body = response.json()
            payload = body if isinstance(body, dict) else {}
        except ValueError:
            payload = {}

        if response.is_error:
            retryable = (
                response.status_code in {409, 429, 503}
                or response.status_code >= 500
            )
            raise ExternalSystemError(
                (
                    f"DQ runner returned HTTP {response.status_code}: "
                    f"{str(payload.get('message') or response.text)[:500]}"
                ),
                system="dq-runner",
                retryable=retryable,
                status_code=response.status_code,
                details={"runner_response": payload},
            )
        return payload
