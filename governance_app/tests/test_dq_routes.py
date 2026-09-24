from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.routes import dq
from app.core.errors import AuthorizationError
from app.core.security import Actor


def actor(*roles: str, subject: str = "operator-1") -> Actor:
    return Actor(
        subject=subject,
        display_name=subject,
        roles=frozenset(roles),
    )


def test_stage_binds_worker_to_authenticated_actor() -> None:
    session = MagicMock()
    settings = MagicMock()
    service = MagicMock()
    service.create_staged_test_case.return_value = {
        "id": "00000000-0000-0000-0000-000000000001",
        "natural_key_hash": "dg_abc",
        "target_entity_fqn": "dev.sales.customer",
        "om_testcase_id": None,
        "status": "STAGED",
    }
    request = SimpleNamespace(
        target_asset_fqn="dev.sales.customer",
        test_definition_fqn="columnValuesToBeNotNull",
        parameter_values={},
        rule_id="r1",
        test_key=None,
        column_name="email",
        worker_id="spoofed-worker",
        rationale=None,
    )

    with patch.object(dq, "DQService", return_value=service):
        response = dq.create_test_case(
            request=request,
            session=session,
            settings=settings,
            actor=actor("governance-agent-bot", subject="agent-bot"),
        )

    assert response.status == "STAGED"
    assert service.create_staged_test_case.call_args.kwargs["worker_id"] == "agent-bot"


def test_agent_cannot_trigger_dq_run() -> None:
    with pytest.raises(AuthorizationError):
        dq.run_test_case(
            registry_id="00000000-0000-0000-0000-000000000001",
            session=MagicMock(),
            settings=MagicMock(),
            actor=actor("governance-agent-bot"),
        )


def test_operator_run_is_durable_when_broker_dispatch_fails() -> None:
    session = MagicMock()
    settings = MagicMock()
    service = MagicMock()
    service.prepare_run.return_value = {
        "run_id": "00000000-0000-0000-0000-000000000099",
        "run_status": "QUEUED",
    }

    with patch.object(dq, "DQService", return_value=service), patch.object(
        dq.run_executable_test_case,
        "delay",
        side_effect=RuntimeError("broker down"),
    ):
        response = dq.run_test_case(
            registry_id="00000000-0000-0000-0000-000000000001",
            session=session,
            settings=settings,
            actor=actor("governance-operator"),
        )

    assert response.status == "QUEUED"
    assert response.task_id is None
    service.prepare_run.assert_called_once_with(
        registry_id="00000000-0000-0000-0000-000000000001",
        actor_id="operator-1",
    )


def test_operator_run_dispatches_same_prepared_run_id() -> None:
    service = MagicMock()
    service.prepare_run.return_value = {
        "run_id": "00000000-0000-0000-0000-000000000099",
        "run_status": "QUEUED",
    }
    task = MagicMock()
    task.id = "00000000-0000-0000-0000-000000000123"

    with patch.object(dq, "DQService", return_value=service), patch.object(
        dq.run_executable_test_case,
        "delay",
        return_value=task,
    ) as delay:
        response = dq.run_test_case(
            registry_id="00000000-0000-0000-0000-000000000001",
            session=MagicMock(),
            settings=MagicMock(),
            actor=actor("governance-admin"),
        )

    delay.assert_called_once_with(
        registry_id="00000000-0000-0000-0000-000000000001",
        run_id="00000000-0000-0000-0000-000000000099",
    )
    assert response.task_id == str(task.id)
