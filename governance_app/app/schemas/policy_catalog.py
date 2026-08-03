from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import ORMModel


SERVER_MANAGED_POLICY_FIELDS = {
    "id",
    "guid",
    "version",
    "createTime",
    "updateTime",
    "createdBy",
    "updatedBy",
}


class RangerPolicyResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    values: list[str] = Field(min_length=1)
    is_excludes: bool = Field(default=False, alias="isExcludes")
    is_recursive: bool = Field(default=False, alias="isRecursive")


class RangerPolicyDocument(BaseModel):
    """Native Apache Ranger policy payload accepted by the policy catalog.

    The model intentionally mirrors Ranger's JSON shape instead of inventing a
    second policy language. Unknown Ranger fields are preserved so newer policy
    features can pass through without a database migration.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    is_enabled: bool = Field(default=True, alias="isEnabled")
    service: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    policy_type: int = Field(default=0, alias="policyType")
    policy_priority: int = Field(default=0, alias="policyPriority")
    description: str = Field(default="", max_length=4000)
    is_audit_enabled: bool = Field(default=True, alias="isAuditEnabled")
    resources: dict[str, RangerPolicyResource]
    policy_items: list[dict[str, Any]] = Field(default_factory=list, alias="policyItems")
    deny_policy_items: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="denyPolicyItems",
    )
    allow_exceptions: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="allowExceptions",
    )
    deny_exceptions: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="denyExceptions",
    )
    data_mask_policy_items: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="dataMaskPolicyItems",
    )
    row_filter_policy_items: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="rowFilterPolicyItems",
    )
    service_type: str | None = Field(default=None, alias="serviceType")
    is_deny_all_else: bool = Field(default=False, alias="isDenyAllElse")

    @model_validator(mode="after")
    def validate_policy_shape(self) -> "RangerPolicyDocument":
        if not self.resources:
            raise ValueError("Ranger policy resources must not be empty")
        for resource_name, resource in self.resources.items():
            if not resource_name.strip():
                raise ValueError("Ranger resource names must not be empty")
            if not any(str(value).strip() for value in resource.values):
                raise ValueError(
                    f"Ranger resource {resource_name!r} must contain at least one value"
                )
        return self

    def native_document(self) -> dict[str, Any]:
        document = self.model_dump(by_alias=True, exclude_none=True)
        for key in SERVER_MANAGED_POLICY_FIELDS:
            document.pop(key, None)
        description = str(document.get("description") or "")
        if " | managed-by=dg-backend;" in description:
            document["description"] = description.split(
                " | managed-by=dg-backend;",
                1,
            )[0]
        return document


class PolicyRecordResponse(ORMModel):
    id: UUID
    policy_key: str
    policy_kind: str
    service: str
    service_type: str | None
    name: str
    document: dict[str, Any]
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class PolicyImportResponse(BaseModel):
    policy: PolicyRecordResponse
    created: bool
    changed: bool


class PolicySyncRequest(BaseModel):
    policy_ids: list[UUID] = Field(default_factory=list, max_length=500)
    correlation_id: str | None = Field(default=None, max_length=128)
