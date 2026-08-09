"""Repository for ClassificationRuleVersion table."""
from __future__ import annotations

import uuid
from typing import Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.classification_rule_version import ClassificationRuleVersion
from app.models.job import utcnow


class ClassificationRuleVersionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active(self, rule_key: str = "default") -> ClassificationRuleVersion | None:
        return (
            self.session.query(ClassificationRuleVersion)
            .filter(
                ClassificationRuleVersion.rule_key == rule_key,
                ClassificationRuleVersion.status == "ACTIVE",
            )
            .first()
        )

    def get_by_checksum(self, rule_key: str, checksum: str) -> ClassificationRuleVersion | None:
        return (
            self.session.query(ClassificationRuleVersion)
            .filter(
                ClassificationRuleVersion.rule_key == rule_key,
                ClassificationRuleVersion.checksum == checksum,
            )
            .first()
        )

    def create(
        self,
        *,
        rule_key: str = "default",
        payload: dict[str, Any],
        checksum: str,
        declared_version: str | None = None,
        created_by: str = "system",
    ) -> ClassificationRuleVersion:
        max_ver = (
            self.session.query(func.max(ClassificationRuleVersion.version))
            .filter(ClassificationRuleVersion.rule_key == rule_key)
            .scalar()
        ) or 0

        next_ver = max_ver + 1

        record = ClassificationRuleVersion(
            rule_key=rule_key,
            version=next_ver,
            status="INACTIVE",
            payload=payload,
            checksum=checksum,
            declared_version=declared_version,
            created_by=created_by,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def activate(self, version_id: uuid.UUID | str, rule_key: str = "default") -> tuple[ClassificationRuleVersion, bool]:
        if isinstance(version_id, str):
            version_id = uuid.UUID(version_id)

        target = self.session.get(ClassificationRuleVersion, version_id)
        if not target:
            raise ValueError(f"ClassificationRuleVersion {version_id} not found")

        if target.status == "ACTIVE":
            return target, False

        # Deactivate previous ACTIVE version for this rule_key
        previous_active = (
            self.session.query(ClassificationRuleVersion)
            .filter(
                ClassificationRuleVersion.rule_key == rule_key,
                ClassificationRuleVersion.status == "ACTIVE",
            )
            .all()
        )
        for prev in previous_active:
            prev.status = "INACTIVE"

        target.status = "ACTIVE"
        target.activated_at = utcnow()
        self.session.flush()
        return target, True
