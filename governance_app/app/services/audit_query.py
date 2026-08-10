from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models.audit import AuditEvent


class AuditQueryService:
    HARD_LIMIT = 100
    MAX_POLICY_KEY_SCAN = 500

    def __init__(self, session: Session) -> None:
        self.session = session

    def summary(
        self,
        *,
        object_type: str | None = None,
        object_id: str | None = None,
        policy_key: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> dict:
        if limit < 1:
            raise ValidationError("limit must be at least 1")
        effective_limit = min(limit, self.HARD_LIMIT)
        if since is not None and until is not None and since > until:
            raise ValidationError("since must be earlier than or equal to until")

        statement = select(AuditEvent)
        if object_type:
            statement = statement.where(AuditEvent.object_type == object_type.strip())
        if object_id:
            statement = statement.where(AuditEvent.object_id == object_id.strip())
        if action:
            statement = statement.where(AuditEvent.action == action.strip())
        if since is not None:
            statement = statement.where(AuditEvent.created_at >= since)
        if until is not None:
            statement = statement.where(AuditEvent.created_at <= until)

        # policy_key is stored inside structured audit details in the existing
        # R4 audit contract. Fetch only a bounded recent window and filter in
        # application code to stay portable across PostgreSQL/SQLite tests.
        fetch_limit = self.MAX_POLICY_KEY_SCAN if policy_key else effective_limit
        rows = list(
            self.session.scalars(
                statement.order_by(AuditEvent.created_at.desc()).limit(fetch_limit)
            )
        )
        if policy_key:
            key = policy_key.strip()
            rows = [
                row
                for row in rows
                if isinstance(row.details, dict) and row.details.get("policy_key") == key
            ]
        rows = rows[:effective_limit]

        return {
            "limit": effective_limit,
            "hard_limit": self.HARD_LIMIT,
            "returned": len(rows),
            "records": [
                {
                    "id": str(row.id),
                    "actor_id": row.actor_id,
                    "actor_name": row.actor_name,
                    "action": row.action,
                    "object_type": row.object_type,
                    "object_id": row.object_id,
                    "correlation_id": row.correlation_id,
                    "details": row.details,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
        }
