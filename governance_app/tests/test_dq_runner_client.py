from __future__ import annotations

import httpx
import pytest

from app.clients.dq_runner import DQRunnerClient
from app.core.errors import ExternalSystemError


def client_with_handler(handler) -> DQRunnerClient:
    client = DQRunnerClient(base_url="http://dq-runner", timeout=10)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://dq-runner",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_success_posts_bounded_execution_identity() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"status": "completed", "exit_code": 0},
        )

    client = client_with_handler(handler)
    try:
        result = client.run_test_case(
            table_fqn="dev.sales.customer",
            test_suite_fqn="dev.sales.customer.testSuite",
            test_case_name="dg_abc",
        )
    finally:
        client.close()

    assert result["status"] == "completed"
    assert captured["path"] == "/dq/run"
    assert "dev.sales.customer" in captured["body"]
    assert "dg_abc" in captured["body"]


@pytest.mark.parametrize("status_code", [409, 429, 503, 500])
def test_transient_runner_statuses_are_retryable(status_code: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"status": "busy", "message": "retry later"},
        )

    client = client_with_handler(handler)
    try:
        with pytest.raises(ExternalSystemError) as caught:
            client.run_test_case(
                table_fqn="dev.sales.customer",
                test_suite_fqn="dev.sales.customer.testSuite",
                test_case_name="dg_abc",
            )
    finally:
        client.close()

    assert caught.value.retryable is True
    assert caught.value.system == "dq-runner"


def test_bad_request_is_permanent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"status": "invalid", "message": "bad identity"},
        )

    client = client_with_handler(handler)
    try:
        with pytest.raises(ExternalSystemError) as caught:
            client.run_test_case(
                table_fqn="dev.sales.customer",
                test_suite_fqn="dev.sales.customer.testSuite",
                test_case_name="dg_abc",
            )
    finally:
        client.close()

    assert caught.value.retryable is False
    assert caught.value.status_code == 400
