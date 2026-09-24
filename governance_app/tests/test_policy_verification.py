from __future__ import annotations

from datetime import timedelta

from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.models.job import utcnow
from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.policy_verification import (
    PolicyRuntimeVerificationService,
    build_verification_plan,
)


def policy(*, decision: str = "ALLOW", subject: str = "alice", with_mask: bool = False, with_filter: bool = False):
    return LogicalDataAccessPolicy.model_validate(
        {
            "subjects": [{"type": "USER", "name": subject}],
            "resource": {
                "catalog": "dev",
                "schema": "sales",
                "table": "customer",
            },
            "access": {"select": decision},
            "masks": {"phone": "MASK"} if with_mask else {},
            "row_filter": "region = 'VN'" if with_filter else None,
        }
    )


def settings(
    *,
    user: str = "alice",
    control_user: str | None = None,
    window: float = 50.0,
) -> Settings:
    return Settings(
        app_env="test",
        trino_readonly_enabled=True,
        trino_readonly_user=user,
        trino_verification_control_user=control_user,
        eventual_consistency_window_seconds=window,
    )


def mask_projection_key(column: str = "phone") -> str:
    import hashlib

    return f"hash:{hashlib.sha256(column.encode()).hexdigest()[:12]}"


class FakeTrino:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = result or {"query_id": "q-1", "rows": [[1]]}
        self.error = error
        self.sql = None

    def query(self, *, sql: str):
        self.sql = sql
        if self.error:
            raise self.error
        return self.result


def denied_error() -> ExternalSystemError:
    return ExternalSystemError(
        "Trino read-only diagnostic query failed",
        system="trino",
        retryable=False,
        details={
            "error_name": "PERMISSION_DENIED",
            "error_type": "USER_ERROR",
            "exception_type": "TrinoUserError",
        },
    )


def test_allow_select_plan_is_deterministic_for_matching_user() -> None:
    plan = build_verification_plan(
        logical_policy=policy(decision="ALLOW"),
        projection_type="ACCESS",
        verification_user="alice",
    )

    assert plan.supported is True
    assert plan.expected == "QUERY_SUCCESS"
    assert plan.sql == 'SELECT 1 AS verification_probe FROM "dev"."sales"."customer" LIMIT 1'


def test_deny_select_plan_expects_access_denied() -> None:
    plan = build_verification_plan(
        logical_policy=policy(decision="DENY"),
        projection_type="ACCESS",
        verification_user="alice",
    )
    assert plan.supported is True
    assert plan.expected == "ACCESS_DENIED"


def test_non_matching_persona_is_unavailable_not_drift() -> None:
    plan = build_verification_plan(
        logical_policy=policy(subject="bob"),
        projection_type="ACCESS",
        verification_user="alice",
    )
    assert plan.supported is False
    assert "not a direct USER subject" in str(plan.reason)


def test_mask_and_row_filter_require_independent_control_identity() -> None:
    mask = build_verification_plan(
        logical_policy=policy(with_mask=True),
        projection_type="MASK",
        projection_key=mask_projection_key(),
        verification_user="alice",
    )
    row_filter = build_verification_plan(
        logical_policy=policy(with_filter=True),
        projection_type="ROW_FILTER",
        verification_user="alice",
    )

    assert mask.supported is False
    assert "CONTROL_USER" in str(mask.reason)
    assert row_filter.supported is False
    assert "CONTROL_USER" in str(row_filter.reason)


def test_allow_success_is_confirmed() -> None:
    trino = FakeTrino()
    result = PolicyRuntimeVerificationService(
        settings(), trino_service=trino
    ).verify(
        logical_policy=policy(decision="ALLOW"),
        projection_type="ACCESS",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=5),
    )

    assert result["status"] == "VERIFICATION_CONFIRMED"
    assert result["observed"] == "QUERY_SUCCESS"
    assert result["matches_expected"] is True
    assert result["query_id"] == "q-1"


def test_deny_access_denied_is_confirmed() -> None:
    trino = FakeTrino(error=denied_error())
    result = PolicyRuntimeVerificationService(
        settings(), trino_service=trino
    ).verify(
        logical_policy=policy(decision="DENY"),
        projection_type="ACCESS",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=5),
    )

    assert result["status"] == "VERIFICATION_CONFIRMED"
    assert result["observed"] == "ACCESS_DENIED"


def test_deny_query_success_is_pending_within_propagation_window() -> None:
    result = PolicyRuntimeVerificationService(
        settings(window=50),
        trino_service=FakeTrino(),
    ).verify(
        logical_policy=policy(decision="DENY"),
        projection_type="ACCESS",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=10),
    )

    assert result["status"] == "VERIFICATION_PENDING"
    assert result["observed"] == "QUERY_SUCCESS"


def test_deny_query_success_becomes_drift_only_after_window() -> None:
    result = PolicyRuntimeVerificationService(
        settings(window=10),
        trino_service=FakeTrino(),
    ).verify(
        logical_policy=policy(decision="DENY"),
        projection_type="ACCESS",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=30),
    )

    assert result["status"] == "RUNTIME_DRIFT"
    assert result["matches_expected"] is False


def test_infrastructure_error_is_verification_error_not_drift() -> None:
    error = ExternalSystemError(
        "Trino connection failed",
        system="trino",
        retryable=True,
        details={"error_name": "CONNECTION_ERROR"},
    )
    result = PolicyRuntimeVerificationService(
        settings(window=1),
        trino_service=FakeTrino(error=error),
    ).verify(
        logical_policy=policy(decision="ALLOW"),
        projection_type="ACCESS",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "VERIFICATION_ERROR"
    assert result["retryable"] is True


def test_mask_hash_plan_uses_subject_and_control_queries() -> None:
    plan = build_verification_plan(
        logical_policy=policy(with_mask=True),
        projection_type="MASK",
        projection_key=mask_projection_key(),
        verification_user="alice",
        control_user="control",
        sample_rows=5,
    )

    assert plan.supported is True
    assert plan.expected == "MASK_HASH_MATCH"
    assert 'CAST("phone" AS varchar)' in str(plan.sql)
    assert "to_hex(sha256(to_utf8" in str(plan.control_sql)
    assert "LIMIT 5" in str(plan.control_sql)


def test_mask_hash_is_confirmed_against_control_transformation() -> None:
    subject = FakeTrino(
        result={
            "query_id": "subject-q",
            "rows": [["A1"], ["B2"]],
        }
    )
    control = FakeTrino(
        result={
            "query_id": "control-q",
            "rows": [["varchar", "A1"], ["varchar", "B2"]],
        }
    )
    result = PolicyRuntimeVerificationService(
        settings(control_user="control"),
        trino_service=subject,
        control_trino_service=control,
    ).verify(
        logical_policy=policy(with_mask=True),
        projection_type="MASK",
        projection_key=mask_projection_key(),
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "VERIFICATION_CONFIRMED"
    assert result["matches_expected"] is True
    assert result["query_id"] == {
        "subject": "subject-q",
        "control": "control-q",
    }


def test_mask_hash_empty_control_sample_is_unavailable_not_drift() -> None:
    result = PolicyRuntimeVerificationService(
        settings(control_user="control", window=1),
        trino_service=FakeTrino(result={"rows": [], "query_id": "s"}),
        control_trino_service=FakeTrino(result={"rows": [], "query_id": "c"}),
    ).verify(
        logical_policy=policy(with_mask=True),
        projection_type="MASK",
        projection_key=mask_projection_key(),
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "VERIFICATION_UNAVAILABLE"
    assert "no non-null mask sample" in result["reason"]


def test_row_filter_requires_control_rows_outside_filter() -> None:
    result = PolicyRuntimeVerificationService(
        settings(control_user="control", window=1),
        trino_service=FakeTrino(result={"rows": [], "query_id": "s"}),
        control_trino_service=FakeTrino(result={"rows": [], "query_id": "c"}),
    ).verify(
        logical_policy=policy(with_filter=True),
        projection_type="ROW_FILTER",
        projection_key="row-filter",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "VERIFICATION_UNAVAILABLE"
    assert "cannot be distinguished from source data" in result["reason"]


def test_row_filter_is_confirmed_when_control_sees_forbidden_rows() -> None:
    result = PolicyRuntimeVerificationService(
        settings(control_user="control", window=1),
        trino_service=FakeTrino(result={"rows": [], "query_id": "s"}),
        control_trino_service=FakeTrino(result={"rows": [[1]], "query_id": "c"}),
    ).verify(
        logical_policy=policy(with_filter=True),
        projection_type="ROW_FILTER",
        projection_key="row-filter",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "VERIFICATION_CONFIRMED"
    assert result["observed"] == {
        "subject_has_violation": False,
        "control_has_violation": True,
    }


def test_row_filter_violation_becomes_drift_after_window() -> None:
    result = PolicyRuntimeVerificationService(
        settings(control_user="control", window=1),
        trino_service=FakeTrino(result={"rows": [[1]], "query_id": "s"}),
        control_trino_service=FakeTrino(result={"rows": [[1]], "query_id": "c"}),
    ).verify(
        logical_policy=policy(with_filter=True),
        projection_type="ROW_FILTER",
        projection_key="row-filter",
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "RUNTIME_DRIFT"
    assert result["matches_expected"] is False


def test_control_identity_must_differ_from_policy_identity() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="control user must differ"):
        Settings(
            app_env="test",
            trino_readonly_enabled=True,
            trino_readonly_user="alice",
            trino_verification_control_user="alice",
        )


def test_mask_hash_non_character_column_is_unavailable() -> None:
    result = PolicyRuntimeVerificationService(
        settings(control_user="control", window=1),
        trino_service=FakeTrino(
            result={"rows": [["1234"]], "query_id": "subject"}
        ),
        control_trino_service=FakeTrino(
            result={"rows": [["bigint", "abcd"]], "query_id": "control"}
        ),
    ).verify(
        logical_policy=policy(with_mask=True),
        projection_type="MASK",
        projection_key=mask_projection_key(),
        ranger_apply_timestamp=utcnow() - timedelta(seconds=100),
    )

    assert result["status"] == "VERIFICATION_UNAVAILABLE"
    assert "character columns" in result["reason"]


def test_row_filter_plan_is_bounded_to_one_violation() -> None:
    plan = build_verification_plan(
        logical_policy=policy(with_filter=True),
        projection_type="ROW_FILTER",
        projection_key="row-filter",
        verification_user="alice",
        control_user="control",
    )

    assert plan.supported is True
    assert "WHERE NOT (region = 'VN')" in str(plan.sql)
    assert str(plan.sql).endswith("LIMIT 1")


def test_group_only_policy_is_explicitly_unavailable_for_automatic_verification() -> None:
    logical = LogicalDataAccessPolicy.model_validate(
        {
            "subjects": [{"type": "GROUP", "name": "pii_readers"}],
            "resource": {
                "catalog": "dev",
                "schema": "sales",
                "table": "customer",
            },
            "access": {"select": "ALLOW"},
            "masks": {},
            "row_filter": None,
        }
    )

    plan = build_verification_plan(
        logical_policy=logical,
        projection_type="ACCESS",
        verification_user="governance-policy-verifier-bot",
    )

    assert plan.supported is False
    assert plan.expected is None
    assert "not a direct USER subject" in str(plan.reason)
