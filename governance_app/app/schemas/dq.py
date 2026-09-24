from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DQTestCaseCreateRequest(BaseModel):
    """Request DTO for POST /api/v1/dq/test-cases (docs/13_IMPLEMENTATION_SPEC.md section 4)."""

    target_asset_fqn: str = Field(min_length=1)
    test_definition_fqn: str = Field(min_length=1)
    parameter_values: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None
    # test_key distinguishes multiple logical TestCases from the same Rule
    # against the same target+definition (B4(a) stable_test_slot_id).
    rule_id: str = Field(min_length=1)
    test_key: str | None = None
    column_name: str | None = None
    worker_id: str = Field(min_length=1)


class DQTestCaseResponse(ORMModel):
    id: str
    natural_key_hash: str
    target_entity_fqn: str | None = None
    om_testcase_id: str | None
    om_testcase_fqn: str | None = None
    om_test_suite_fqn: str | None = None
    status: Literal["STAGED", "APPROVED", "EXECUTABLE", "FAILED"]
    materialization_task_id: str | None = None
    run_generation: int = 0
    run_id: str | None = None
    run_status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"] | None = None
    run_started_at: datetime | None = None
    run_finished_at: datetime | None = None
    run_requested_by: str | None = None
    run_error: str | None = None
    last_result: dict[str, Any] = Field(default_factory=dict)


class DQRunAcceptedResponse(BaseModel):
    registry_id: str
    run_id: str
    status: Literal["QUEUED"]
    task_id: str | None = None
