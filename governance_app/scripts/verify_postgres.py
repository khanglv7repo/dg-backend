from __future__ import annotations

import os
import threading
import time
import uuid
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.enums import JobType
from app.repositories.jobs import JobRepository


def redact_url(url_str: str) -> str:
    parsed = urlparse(url_str)
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}@", ":****@")
        return parsed._replace(netloc=netloc).geturl()
    return url_str


def main():
    db_url = os.getenv("DATABASE_URL", get_settings().database_url)
    redacted_url = redact_url(db_url)

    engine = create_engine(db_url)
    dialect_name = engine.dialect.name

    print(f"1. Effective DATABASE_URL: {redacted_url}")
    print(f"   SQLAlchemy Dialect: {dialect_name}")

    with engine.connect() as conn:
        pg_version = conn.execute(text("SELECT version()")).scalar()
        print(f"   PostgreSQL Version: {pg_version}")

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    required_tables = [
        "governance_jobs",
        "classification_runs",
        "policy_reconciliations",
        "access_verifications",
        "audit_events",
        "data_value_scan_runs",
        "integration_watermarks",
        "alembic_version",
    ]

    print("\n3. Verifying required tables in database:")
    missing = []
    for table in required_tables:
        if table in existing_tables:
            print(f"   [OK] Table '{table}' exists")
        else:
            print(f"   [MISSING] Table '{table}' NOT found")
            missing.append(table)

    if missing:
        raise RuntimeError(f"Missing required tables: {missing}")

    # 5. Test concurrent claim verification
    print("\n5. Testing concurrent governance_jobs claiming with 2 worker sessions...")
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # Seed 10 queued jobs
    with Session() as seed_session, seed_session.begin():
        repo = JobRepository(seed_session)
        seed_job_ids = []
        for i in range(10):
            job = repo.enqueue(
                job_type=JobType.CLASSIFY_ASSET,
                idempotency_key=f"concurrent-test-{uuid.uuid4()}",
                payload={"test_index": i},
            )
            seed_job_ids.append(job.id)

    worker_a_jobs = []
    worker_b_jobs = []

    barrier = threading.Barrier(2)

    def worker_a_task():
        with Session() as session_a, session_a.begin():
            repo_a = JobRepository(session_a)
            jobs_a = repo_a.claim_batch(worker_id="worker-A", limit=10)
            worker_a_jobs.extend([j.id for j in jobs_a])
            barrier.wait()  # Wait for Worker B to attempt claim while lock is held
            time.sleep(0.2)  # Hold transaction lock briefly

    def worker_b_task():
        barrier.wait()  # Synchronize to run claim while Worker A holds FOR UPDATE SKIP LOCKED
        with Session() as session_b, session_b.begin():
            repo_b = JobRepository(session_b)
            jobs_b = repo_b.claim_batch(worker_id="worker-B", limit=10)
            worker_b_jobs.extend([j.id for j in jobs_b])

    t1 = threading.Thread(target=worker_a_task)
    t2 = threading.Thread(target=worker_b_task)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    overlap = set(worker_a_jobs).intersection(set(worker_b_jobs))
    print(f"   Worker A claimed: {len(worker_a_jobs)} jobs")
    print(f"   Worker B claimed: {len(worker_b_jobs)} jobs")
    print(f"   Overlapping jobs between Worker A and Worker B: {len(overlap)}")

    if overlap:
        raise RuntimeError(
            f"CONCURRENCY FAILURE: Overlapping jobs claimed by both workers! {overlap}"
        )

    print("   [PASS] Concurrent claim verification succeeded. Zero overlapping claims.")


if __name__ == "__main__":
    main()
