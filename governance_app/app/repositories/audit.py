from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AccessVerification, AuditEvent, PolicyReconciliation


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        actor_id: str,
        actor_name: str,
        action: str,
        object_type: str,
        object_id: str,
        correlation_id: str | None,
        details: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_id=actor_id,
            actor_name=actor_name,
            action=action,
            object_type=object_type,
            object_id=object_id,
            correlation_id=correlation_id,
            details=details or {},
        )
        self.session.add(event)
        return event

    def record_reconciliation(self, **values) -> PolicyReconciliation:
        record = PolicyReconciliation(**values)
        self.session.add(record)
        return record

    def record_verification(self, **values) -> AccessVerification:
        record = AccessVerification(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def list_verification_group(self, group_id: str) -> list[AccessVerification]:
        return list(
            self.session.scalars(
                select(AccessVerification)
                .where(AccessVerification.verification_group_id == group_id)
                .order_by(AccessVerification.created_at.asc())
            )
        )
