from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import ORMModel


class ClassificationRuleSetResponse(ORMModel):
    id: UUID
    name: str
    declared_version: str | None
    document: dict[str, Any]
    document_sha256: str
    status: str
    created_by: str
    created_by_name: str
    created_at: datetime
    activated_at: datetime | None


class ClassificationRuleImportResponse(BaseModel):
    rule_set: ClassificationRuleSetResponse
    created: bool
    activated: bool
