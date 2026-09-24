from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db.base import Base
from app.models.event_inbox import EventInbox
from app.models.job import utcnow
from app.tasks import recovery as recovery_task


def _factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    return engine, factory


def _event(*, event_id: str, created_at, status: str = "RECEIVED") -> EventInbox:
    return EventInbox(
        event_id=event_id,
        event_type="entityFieldsChanged",
        entity_type="table",
        entity_fqn="dev.sales.customer",
        payload={},
        purposes=["TAG_SYNC"],
        dispatched_purposes=[],
        dispatched_tasks={},
        status=status,
        correlation_id=f"corr-{event_id}",
        created_at=created_at,
    )


def test_recovery_ignores_fresh_inbox_rows() -> None:
    engine, factory = _factory()
    try:
        with factory() as db:
            db.add(
                _event(
                    event_id="fresh",
                    created_at=utcnow() - timedelta(seconds=5),
                )
            )
            db.commit()

        with patch.object(
            recovery_task, "SessionLocal", factory
        ), patch.object(
            recovery_task.sync_tags_to_ranger, "delay"
        ) as delay:
            result = recovery_task.retry_unfinished_workflows.run()

        assert result == {"recovered": 0, "poisoned": 0}
        delay.assert_not_called()

        with factory() as db:
            record = db.query(EventInbox).filter_by(event_id="fresh").one()
            assert record.status == "RECEIVED"
            assert record.dispatched_purposes == []
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_recovery_dispatches_stale_row_and_marks_processed_same_sweep() -> None:
    engine, factory = _factory()
    try:
        with factory() as db:
            db.add(
                _event(
                    event_id="stale",
                    created_at=utcnow() - timedelta(seconds=120),
                )
            )
            db.commit()

        task = MagicMock()
        task.id = "celery-tag-recovery-1"
        with patch.object(
            recovery_task, "SessionLocal", factory
        ), patch.object(
            recovery_task.sync_tags_to_ranger,
            "delay",
            return_value=task,
        ) as delay:
            result = recovery_task.retry_unfinished_workflows.run()

        assert result == {"recovered": 1, "poisoned": 0}
        delay.assert_called_once_with(
            entity_type="table",
            entity_fqn="dev.sales.customer",
            correlation_id="corr-stale",
        )

        with factory() as db:
            record = db.query(EventInbox).filter_by(event_id="stale").one()
            assert record.status == "PROCESSED"
            assert record.dispatched_purposes == ["TAG_SYNC"]
            assert record.dispatched_tasks["TAG_SYNC"] == "celery-tag-recovery-1"
            assert record.dispatched_tasks["_retry_count"] == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
