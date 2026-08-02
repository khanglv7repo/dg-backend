from app.models.enums import JobStatus, JobType
from app.repositories.jobs import JobRepository


def test_enqueue_is_idempotent_and_claims_once(session) -> None:
    repository = JobRepository(session)
    first = repository.enqueue(
        job_type=JobType.CLASSIFY_ASSET,
        idempotency_key="same-logical-work",
        payload={"entity_fqn": "hive.sales.customers"},
    )
    second = repository.enqueue(
        job_type=JobType.CLASSIFY_ASSET,
        idempotency_key="same-logical-work",
        payload={"entity_fqn": "different-payload-is-ignored"},
    )
    session.commit()

    assert first.id == second.id

    with session.begin():
        claimed = repository.claim_batch(worker_id="test-worker", limit=10)

    assert [job.id for job in claimed] == [first.id]
    assert claimed[0].status == JobStatus.RUNNING.value
    assert claimed[0].attempt_count == 1


def test_worker_roles_claim_only_their_job_types(session) -> None:
    repository = JobRepository(session)
    execution = repository.enqueue(
        job_type=JobType.CLASSIFY_ASSET,
        idempotency_key="execution-job",
        payload={},
    )
    agent = repository.enqueue(
        job_type=JobType.AGENT_CLASSIFY,
        idempotency_key="agent-job",
        payload={},
    )
    session.commit()

    with session.begin():
        agent_claim = repository.claim_batch(
            worker_id="agent-worker",
            limit=10,
            allowed_job_types={JobType.AGENT_CLASSIFY},
        )
    assert [item.id for item in agent_claim] == [agent.id]

    with session.begin():
        execution_claim = repository.claim_batch(
            worker_id="execution-worker",
            limit=10,
            excluded_job_types={JobType.AGENT_CLASSIFY},
        )
    assert [item.id for item in execution_claim] == [execution.id]
