from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import ORMModel

POLICY_KEY_PATTERN = re.compile(r"^[^;\r\n]+$")
SUPPORTED_TRINO_ACCESS_OPERATIONS = frozenset(
    {
        "select",
        "insert",
        "create",
        "drop",
        "delete",
        "use",
        "alter",
        "grant",
        "revoke",
        "show",
        "impersonate",
        "execute",
        "read_sysinfo",
        "write_sysinfo",
        "all",
    }
)


class SubjectType(str, Enum):
    USER = "USER"
    GROUP = "GROUP"


class AccessDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class LogicalMaskIntent(str, Enum):
    MASK = "MASK"


class PolicySubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SubjectType
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("subject name must not be empty")
        return normalized


class PolicyResource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    catalog: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(
        min_length=1,
        max_length=255,
        alias="schema",
        serialization_alias="schema",
    )
    table: str = Field(min_length=1, max_length=255)

    @field_validator("catalog", "schema_name", "table")
    @classmethod
    def normalize_resource_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource names must not be empty")
        return normalized


class LogicalDataAccessPolicy(BaseModel):
    """Backend-owned logical data-access policy.

    This is intentionally not a native RangerPolicy payload. It is deterministic
    business intent that can later be compiled into one or more Ranger policies.
    """

    model_config = ConfigDict(extra="forbid")

    subjects: list[PolicySubject] = Field(min_length=1)
    resource: PolicyResource
    access: dict[str, AccessDecision] = Field(default_factory=dict)
    masks: dict[str, LogicalMaskIntent] = Field(default_factory=dict)
    row_filter: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_maps(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        values = dict(raw)

        access_raw = values.get("access") or {}
        if not isinstance(access_raw, dict):
            return values
        normalized_access: dict[str, Any] = {}
        for operation, decision in access_raw.items():
            key = str(operation).strip().lower()
            if not key:
                raise ValueError("access operation names must not be empty")
            if key in normalized_access and normalized_access[key] != decision:
                raise ValueError(f"conflicting access decision for {key!r}")
            normalized_access[key] = (
                str(decision).strip().upper()
                if isinstance(decision, str)
                else decision
            )
        values["access"] = normalized_access

        masks_raw = values.get("masks") or {}
        if not isinstance(masks_raw, dict):
            return values
        normalized_masks: dict[str, Any] = {}
        for column, intent in masks_raw.items():
            key = str(column).strip()
            if not key:
                raise ValueError("mask column names must not be empty")
            if key in normalized_masks and normalized_masks[key] != intent:
                raise ValueError(f"conflicting mask intent for {key!r}")
            normalized_masks[key] = (
                str(intent).strip().upper()
                if isinstance(intent, str)
                else intent
            )
        values["masks"] = normalized_masks
        return values

    @field_validator("access")
    @classmethod
    def validate_operations(
        cls,
        value: dict[str, AccessDecision],
    ) -> dict[str, AccessDecision]:
        unsupported = sorted(set(value) - SUPPORTED_TRINO_ACCESS_OPERATIONS)
        if unsupported:
            raise ValueError(
                "unsupported Trino access operations: " + ", ".join(unsupported)
            )
        return value

    @field_validator("row_filter")
    @classmethod
    def normalize_row_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("row_filter must be a non-empty expression or null")
        return normalized

    @model_validator(mode="after")
    def validate_semantic_content(self) -> "LogicalDataAccessPolicy":
        if not self.access and not self.masks and self.row_filter is None:
            raise ValueError(
                "policy must contain access, masks, or row_filter intent"
            )
        return self

    def normalized_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def checksum(self) -> str:
        document = self.normalized_document()
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_policy_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("policy_key must not be empty")
    if len(normalized) > 512:
        raise ValueError("policy_key must not exceed 512 characters")
    if not POLICY_KEY_PATTERN.match(normalized):
        raise ValueError("policy_key must not contain semicolon or line breaks")
    return normalized


class CreatePolicyVersionRequest(BaseModel):
    logical_policy: LogicalDataAccessPolicy


class PreviewPolicyRequest(BaseModel):
    logical_policy: LogicalDataAccessPolicy


class RollbackPolicyRequest(BaseModel):
    target_version: int = Field(ge=1)


class PolicyVersionResponse(ORMModel):
    id: UUID
    policy_key: str
    version: int
    status: str
    logical_policy: dict[str, Any]
    checksum: str
    created_by: str
    created_at: datetime
    activated_at: datetime | None


class ProjectionResponse(ORMModel):
    id: UUID
    policy_version_id: UUID
    projection_type: str
    projection_key: str
    ranger_service: str
    ranger_policy_name: str
    ranger_policy_id: str | None
    ranger_policy_guid: str | None
    desired_checksum: str
    observed_checksum: str | None
    sync_status: str
    reconciliation_details: dict[str, Any]
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    last_reconciled_at: datetime | None


class PreviewProjection(BaseModel):
    projection_type: str
    projection_key: str
    ranger_policy_name: str
    desired_checksum: str
    action: str
    owned_current: bool | None = None
    current_policy_id: str | None = None


class PolicyPreviewResponse(BaseModel):
    policy_key: str
    candidate_version: int
    logical_checksum: str
    projections: list[PreviewProjection]
    retire_policy_names: list[str] = Field(default_factory=list)


class ActivationResponse(BaseModel):
    version: PolicyVersionResponse
    dispatched: bool


class PolicyStatusResponse(BaseModel):
    policy_key: str
    active_version: PolicyVersionResponse | None
    projections: list[ProjectionResponse]
