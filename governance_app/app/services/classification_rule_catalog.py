from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, ValidationError
from app.repositories.audit import AuditRepository
from app.repositories.classification_rule_sets import (
    ClassificationRuleSetRepository,
)
from app.repositories.classification_rule_versions import (
    ClassificationRuleVersionRepository,
)
from app.rules.classification import ClassificationRuleEngine


MAX_CLASSIFICATION_RULE_FILE_BYTES = 2 * 1024 * 1024


class ClassificationRuleCatalogService:
    """DB-backed classification rule catalog powered by authoritative classification_rule_versions.

    JSON files are import transport only. PostgreSQL owns the runtime active
    rule version used by classification.
    """

    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session
        self.version_repo = ClassificationRuleVersionRepository(session)
        self.legacy_repo = ClassificationRuleSetRepository(session)
        self.audit = AuditRepository(session)

    def get_active(self):
        active_ver = self.version_repo.get_active()
        if active_ver is not None:
            return active_ver

        active_legacy = self.legacy_repo.get_active()
        if active_legacy is None:
            raise ConfigurationError(
                "no active classification rule version; "
                "import a JSON rule file first"
            )
        return active_legacy

    def active_engine(
        self,
    ) -> ClassificationRuleEngine:
        active = self.get_active()
        doc = getattr(active, "payload", None) or getattr(active, "document", None)
        return ClassificationRuleEngine(doc)

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

        ver_record = self.version_repo.get_by_checksum("default", document_sha256)
        created = ver_record is None

        if ver_record is None:
            ver_record = self.version_repo.create(
                rule_key="default",
                payload=engine.document,
                checksum=document_sha256,
                declared_version=engine.declared_version,
                created_by=actor_id,
            )

        activated = False
        if activate:
            ver_record, activated = self.version_repo.activate(ver_record.id, "default")

        # Also sync to legacy repo for backwards compatibility
        legacy_rec = self.legacy_repo.get_by_sha256(name="default", document_sha256=document_sha256)
        if legacy_rec is None:
            legacy_rec = self.legacy_repo.create(
                name="default",
                declared_version=engine.declared_version,
                document=engine.document,
                document_sha256=document_sha256,
                created_by=actor_id,
                created_by_name=actor_name,
            )
        if activate:
            self.legacy_repo.activate(legacy_rec.id)

        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=(
                "CLASSIFICATION_RULE_VERSION_IMPORTED"
                if created
                else "CLASSIFICATION_RULE_VERSION_REIMPORT"
            ),
            object_type="classification_rule_version",
            object_id=str(ver_record.id),
            correlation_id=correlation_id,
            details={
                "filename": filename,
                "version": ver_record.version,
                "declared_version": ver_record.declared_version,
                "checksum": ver_record.checksum,
                "rule_count": len(engine.rules),
                "activated": activate,
                "status": ver_record.status,
            },
        )

        return ver_record, created, activated

    def activate(
        self,
        rule_set_id,
        *,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ):
        record, changed = self.version_repo.activate(rule_set_id, "default")
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=(
                "CLASSIFICATION_RULE_VERSION_ACTIVATED"
                if changed
                else "CLASSIFICATION_RULE_VERSION_ALREADY_ACTIVE"
            ),
            object_type="classification_rule_version",
            object_id=str(record.id),
            correlation_id=correlation_id,
            details={
                "version": record.version,
                "checksum": record.checksum,
            },
        )
        return record, changed
