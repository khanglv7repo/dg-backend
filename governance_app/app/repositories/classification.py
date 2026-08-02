
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.classification import ClassificationRun


class ClassificationRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **values) -> ClassificationRun:
        existing = self.session.scalar(
            select(ClassificationRun).where(
                ClassificationRun.event_id == values["event_id"],
                ClassificationRun.source_kind == values["source_kind"],
                ClassificationRun.source_version == values["source_version"],
            )
        )
        if existing:
            return existing
        record = ClassificationRun(**values)
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
            return record
        except IntegrityError:
            existing = self.session.scalar(
                select(ClassificationRun).where(
                    ClassificationRun.event_id == values["event_id"],
                    ClassificationRun.source_kind == values["source_kind"],
                    ClassificationRun.source_version == values["source_version"],
                )
            )
            if not existing:
                raise
            return existing

    def get(self, run_id: uuid.UUID | str) -> ClassificationRun:
        identifier = uuid.UUID(str(run_id))
        record = self.session.get(ClassificationRun, identifier)
        if not record:
            raise NotFoundError(f"classification run {identifier} was not found")
        return record

    def set_openmetadata_suggestions(
        self, run_id: uuid.UUID | str, suggestion_ids: list[str]
    ) -> ClassificationRun:
        record = self.get(run_id)
        record.openmetadata_suggestion_ids = sorted(set(suggestion_ids))
        self.session.flush()
        return record
