
from app.clients.trino import QueryObservation
from app.services.verification import VerificationService


class FakeExecutor:
    def execute(self, *, identity: str, sql: str) -> QueryObservation:
        return QueryObservation(allowed=identity == "allowed-user", duration_ms=2.5)


def test_grouped_verification_completes_without_backend_proposal_state(session) -> None:
    service = VerificationService(session, FakeExecutor())
    with session.begin():
        first = service.verify(
            verification_group_id="group-1",
            verification_total=2,
            policy_key="policy-1",
            identity="allowed-user",
            sql="SELECT email FROM hive.sales.customers LIMIT 1",
            expected_allowed=True,
            classification_run_id="run-1",
            correlation_id="corr",
        )
    assert first["complete"] is False

    with session.begin():
        second = service.verify(
            verification_group_id="group-1",
            verification_total=2,
            policy_key="policy-1",
            identity="denied-user",
            sql="SELECT email FROM hive.sales.customers LIMIT 1",
            expected_allowed=False,
            classification_run_id="run-1",
            correlation_id="corr",
        )

    assert second["complete"] is True
    assert second["passed"] is True
