from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.jobs.processor import JobProcessor
from app.models.enums import JobType
from app.repositories.jobs import JobRepository

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        role: str,
        worker_id: str,
        allowed_job_types: set[JobType] | None = None,
        excluded_job_types: set[JobType] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.role = role
        self.worker_id = worker_id
        self.allowed_job_types = allowed_job_types
        self.excluded_job_types = excluded_job_types
        self.processor = JobProcessor(
            session_factory=SessionLocal,
            settings=self.settings,
            worker_id=worker_id,
            worker_role=role,
        )
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run(self) -> None:
        try:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        except ValueError:
            pass
        logger.info("worker started", extra={"worker_role": self.role, "worker_id": self.worker_id})
        last_recovery = 0.0
        while self.running:
            now = time.monotonic()
            with SessionLocal() as session, session.begin():
                if now - last_recovery >= 60:
                    stale_before = datetime.now(UTC) - timedelta(
                        seconds=self.settings.worker_stale_after_seconds
                    )
                    recovered = JobRepository(session).recover_stale(stale_before=stale_before)
                    if recovered:
                        logger.warning("recovered stale jobs", extra={"count": recovered})
                    last_recovery = now
                jobs = JobRepository(session).claim_batch(
                    worker_id=self.worker_id,
                    limit=self.settings.worker_claim_batch,
                    allowed_job_types=self.allowed_job_types,
                    excluded_job_types=self.excluded_job_types,
                )
                job_ids = [job.id for job in jobs]
            if not job_ids:
                time.sleep(self.settings.worker_poll_seconds)
                continue
            for job_id in job_ids:
                if not self.running:
                    break
                self.processor.process(job_id)
        logger.info("worker stopped", extra={"worker_role": self.role, "worker_id": self.worker_id})


def configure_worker_logging() -> Settings:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    return settings
