from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, ValidationError
from app.repositories.audit import AuditRepository
from app.repositories.classification_rule_sets import (
    ClassificationRuleSetRepository,
)
from app.rules.classification import ClassificationRuleEngine


MAX_CLASSIFICATION_RULE_FILE_BYTES = 2 * 1024 * 1024


class ClassificationRuleCatalogService:
    """DB-backed classification rule-set catalog.

    JSON files are import transport only. PostgreSQL owns the runtime active
    rule set used by classification.
    """

    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session
        self.repository = (
            ClassificationRuleSetRepository(
                session
            )
        )
        self.audit = AuditRepository(session)

    def list_rule_sets(self):
        return self.repository.list_all()

    def get_active(self):
        active = self.repository.get_active()
        if active is None:
            raise ConfigurationError(
                "no active classification rule set; "
                "import a JSON rule file first"
            )
        return active

    def active_engine(
        self,
    ) -> ClassificationRuleEngine:
        active = self.get_active()
        return ClassificationRuleEngine(
            active.document
        )

    def import_json(
        self,
        payload: bytes,
        *,
        filename: str | None,
        actor_id: str,
        actor_name: str,
        activate: bool = True,
        correlation_id: str | None = None,
    ):
        if not payload:
            raise ValidationError(
                "classification rule file is empty"
            )
        if len(payload) > MAX_CLASSIFICATION_RULE_FILE_BYTES:
            raise ValidationError(
                "classification rule file exceeds 2 MiB limit"
            )
        if (
            filename
            and not filename.lower().endswith(".json")
        ):
            raise ValidationError(
                "classification rule upload accepts JSON files only"
            )

        try:
            engine = ClassificationRuleEngine.from_json_bytes(
                payload
            )
        except ConfigurationError as exc:
            raise ValidationError(
                exc.message,
                details=exc.details,
            ) from exc

        canonical = json.dumps(
            engine.document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        document_sha256 = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        record = self.repository.get_by_sha256(
            name="default",
            document_sha256=document_sha256,
        )
        created = record is None

        if record is None:
            record = self.repository.create(
                name="default",
                declared_version=engine.declared_version,
                document=engine.document,
                document_sha256=document_sha256,
                created_by=actor_id,
                created_by_name=actor_name,
            )

        activated = False
        if activate:
            record, activated = (
                self.repository.activate(
                    record.id
                )
            )

        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=(
                "CLASSIFICATION_RULE_SET_IMPORTED"
                if created
                else "CLASSIFICATION_RULE_SET_REIMPORT"
            ),
            object_type="classification_rule_set",
            object_id=str(record.id),
            correlation_id=correlation_id,
            details={
                "filename": filename,
                "declared_version":
                    record.declared_version,
                "document_sha256":
                    record.document_sha256,
                "rule_count":
                    len(engine.rules),
                "activated": activate,
                "status": record.status,
            },
        )

        return record, created, activated

    def activate(
        self,
        rule_set_id,
        *,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ):
        record, changed = (
            self.repository.activate(
                rule_set_id
            )
        )
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=(
                "CLASSIFICATION_RULE_SET_ACTIVATED"
                if changed
                else "CLASSIFICATION_RULE_SET_ALREADY_ACTIVE"
            ),
            object_type="classification_rule_set",
            object_id=str(record.id),
            correlation_id=correlation_id,
            details={
                "declared_version":
                    record.declared_version,
                "document_sha256":
                    record.document_sha256,
            },
        )
        return record, changed
