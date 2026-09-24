from __future__ import annotations

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
    om_testcase_id: str | None
    status: Literal["STAGED", "EXECUTABLE", "FAILED"]
