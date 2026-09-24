from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.trino_verification import (
    RUNTIME_DRIFT,
    VERIFICATION_CONFIRMED,
    VERIFICATION_INCONCLUSIVE,
    VERIFICATION_UNAVAILABLE,
    TrinoRuntimeVerificationService,
)


def settings() -> Settings:
    return Settings(
        app_env="test",
        trino_readonly_enabled=True,
        trino_readonly_user="alice",
        eventual_consistency_window_seconds=50,
    )


def policy(
    *,
    access: dict | None = None,
    masks: dict | None = None,
    row_filter: str | None = None,
) -> LogicalDataAccessPolicy:
    return LogicalDataAccessPolicy.model_validate(
        {
            "subjects": [{"type": "USER", "name": "alice"}],
            "resource": {
                "catalog": "dev",
                "schema": "sales",
                "table": "customer",
            },
            "access": access or {},
            "masks": masks or {},
            "row_filter": row_filter,
        }
    )


def old_apply_time() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=120)


def test_access_allow_success_is_confirmed() -> None:
    trino = MagicMock()
    trino.query.return_value = {
        "rows": [[1]],
        "row_count_returned": 1,
        "query_id": "q-allow",
    }
    result = TrinoRuntimeVerificationService(settings(), trino=trino).verify(
        projection_type="ACCESS",
        projection_key="access",
        logical_policy=policy(access={"select": "ALLOW"}),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == VERIFICATION_CONFIRMED
    assert result.details["observed_allowed"] is True


def test_access_deny_permission_denied_is_confirmed() -> None:
    trino = MagicMock()
    trino.query.side_effect = ExternalSystemError(
        "denied",
        system="trino",
        details={"error_name": "PERMISSION_DENIED", "error_type": "USER_ERROR"},
    )
    result = TrinoRuntimeVerificationService(settings(), trino=trino).verify(
        projection_type="ACCESS",
        projection_key="access",
        logical_policy=policy(access={"select": "DENY"}),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == VERIFICATION_CONFIRMED
    assert result.details["observed_allowed"] is False


def test_access_deny_but_query_succeeds_is_runtime_drift_after_window() -> None:
    trino = MagicMock()
    trino.query.return_value = {
        "rows": [[1]],
        "row_count_returned": 1,
        "query_id": "q-drift",
    }
    result = TrinoRuntimeVerificationService(settings(), trino=trino).verify(
        projection_type="ACCESS",
        projection_key="access",
        logical_policy=policy(access={"select": "DENY"}),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == RUNTIME_DRIFT


def test_row_filter_positive_violation_is_runtime_drift() -> None:
    trino = MagicMock()
    trino.query.return_value = {
        "rows": [[3]],
        "row_count_returned": 1,
        "query_id": "q-filter-drift",
    }
    result = TrinoRuntimeVerificationService(settings(), trino=trino).verify(
        projection_type="ROW_FILTER",
        projection_key="row-filter",
        logical_policy=policy(row_filter="region = 'VN'"),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == RUNTIME_DRIFT
    assert result.details["violations"] == 3


def test_row_filter_zero_violation_without_baseline_is_inconclusive() -> None:
    trino = MagicMock()
    trino.query.return_value = {
        "rows": [[0]],
        "row_count_returned": 1,
        "query_id": "q-filter-zero",
    }
    result = TrinoRuntimeVerificationService(settings(), trino=trino).verify(
        projection_type="ROW_FILTER",
        projection_key="row-filter",
        logical_policy=policy(row_filter="region = 'VN'"),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == VERIFICATION_INCONCLUSIVE


def test_mask_without_baseline_is_unavailable_and_never_confirmed() -> None:
    trino = MagicMock()
    result = TrinoRuntimeVerificationService(settings(), trino=trino).verify(
        projection_type="MASK",
        projection_key="hash:abc",
        logical_policy=policy(masks={"phone": "MASK"}),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == VERIFICATION_UNAVAILABLE
    trino.query.assert_not_called()


def test_persona_not_direct_policy_user_is_unavailable() -> None:
    cfg = Settings(
        app_env="test",
        trino_readonly_enabled=True,
        trino_readonly_user="bob",
    )
    trino = MagicMock()
    result = TrinoRuntimeVerificationService(cfg, trino=trino).verify(
        projection_type="ACCESS",
        projection_key="access",
        logical_policy=policy(access={"select": "ALLOW"}),
        ranger_apply_timestamp=old_apply_time(),
    )
    assert result.status == VERIFICATION_UNAVAILABLE
    trino.query.assert_not_called()
