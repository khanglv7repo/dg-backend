from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.classification_rule_set import ClassificationRuleSet
from app.models.job import utcnow


class ClassificationRuleSetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(
        self,
        *,
        name: str = "default",
    ) -> list[ClassificationRuleSet]:
        return list(
            self.session.scalars(
                select(ClassificationRuleSet)
                .where(ClassificationRuleSet.name == name)
                .order_by(ClassificationRuleSet.created_at.desc())
            )
        )

    def get(
        self,
        rule_set_id: uuid.UUID | str,
    ) -> ClassificationRuleSet:
        identifier = uuid.UUID(str(rule_set_id))
        record = self.session.get(
            ClassificationRuleSet,
            identifier,
        )
        if record is None:
            raise NotFoundError(
                f"classification rule set {identifier} was not found"
            )
        return record

    def get_active(
        self,
        *,
        name: str = "default",
    ) -> ClassificationRuleSet | None:
        return self.session.scalar(
            select(ClassificationRuleSet).where(
                ClassificationRuleSet.name == name,
                ClassificationRuleSet.status == "ACTIVE",
            )
        )

    def get_by_sha256(
        self,
        *,
        name: str,
        document_sha256: str,
    ) -> ClassificationRuleSet | None:
        return self.session.scalar(
            select(ClassificationRuleSet).where(
                ClassificationRuleSet.name == name,
                ClassificationRuleSet.document_sha256
                == document_sha256,
            )
        )

    def create(
        self,
        *,
        name: str,
        declared_version: str | None,
        document: dict,
        document_sha256: str,
        created_by: str,
        created_by_name: str,
    ) -> ClassificationRuleSet:
        record = ClassificationRuleSet(
            name=name,
            declared_version=declared_version,
            document=document,
            document_sha256=document_sha256,
            status="INACTIVE",
            created_by=created_by,
            created_by_name=created_by_name,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def activate(
        self,
        rule_set_id: uuid.UUID | str,
    ) -> tuple[ClassificationRuleSet, bool]:
        target = self.get(rule_set_id)
        already_active = target.status == "ACTIVE"

        active_records = list(
            self.session.scalars(
                select(ClassificationRuleSet).where(
                    ClassificationRuleSet.name == target.name,
                    ClassificationRuleSet.status == "ACTIVE",
                    ClassificationRuleSet.id != target.id,
                )
            )
        )
        for record in active_records:
            record.status = "INACTIVE"

        if not already_active:
            target.status = "ACTIVE"
            target.activated_at = utcnow()

        self.session.flush()
        return target, not already_active
