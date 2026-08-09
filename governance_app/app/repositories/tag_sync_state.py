"""Repository for TagSyncState persistence."""
from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session

from app.models.tag_sync_state import TagSyncState


class TagSyncStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_entity(self, entity_type: str, entity_fqn: str) -> TagSyncState | None:
        return (
            self.session.query(TagSyncState)
            .filter(
                TagSyncState.entity_type == entity_type,
                TagSyncState.entity_fqn == entity_fqn,
            )
            .first()
        )

    def record_sync(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        status: str = "SYNCHRONIZED",
        checksum: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> TagSyncState:
        record = self.get_by_entity(entity_type, entity_fqn)
        if record is None:
            record = TagSyncState(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                status=status,
                checksum=checksum,
                details=details or {},
            )
            self.session.add(record)
        else:
            record.status = status
            record.checksum = checksum
            record.details = details or {}
        self.session.flush()
        return record
