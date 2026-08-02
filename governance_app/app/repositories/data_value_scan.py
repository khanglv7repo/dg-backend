from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.data_value_scan import DataValueScanRun


class DataValueScanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **values) -> DataValueScanRun:
        record = DataValueScanRun(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, run_id: uuid.UUID | str) -> DataValueScanRun:
        identifier = uuid.UUID(str(run_id))
        record = self.session.get(DataValueScanRun, identifier)
        if not record:
            raise NotFoundError(f"data value scan run {identifier} was not found")
        return record

    def list_by_entity(self, entity_fqn: str, limit: int = 50) -> list[DataValueScanRun]:
        return list(
            self.session.scalars(
                select(DataValueScanRun)
                .where(DataValueScanRun.entity_fqn == entity_fqn)
                .order_by(DataValueScanRun.created_at.desc())
                .limit(limit)
            )
        )
