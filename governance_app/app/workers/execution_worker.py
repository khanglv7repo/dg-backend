from app.models.enums import JobType
from app.workers.base import Worker, configure_worker_logging


def main() -> None:
    settings = configure_worker_logging()
    Worker(
        role="execution",
        worker_id=settings.execution_worker_id,
        excluded_job_types={JobType.AGENT_CLASSIFY},
        settings=settings,
    ).run()


if __name__ == "__main__":
    main()
