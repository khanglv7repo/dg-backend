"""Evidence-based Trino runtime verification for Ranger projections.

This module deliberately separates Ranger convergence from runtime enforcement.
It only emits RUNTIME_DRIFT when an observation directly contradicts policy
intent and the configured Ranger->Trino propagation window has elapsed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.errors import ExternalSystemError, ValidationError
from app.models.job import utcnow
from app.schemas.data_access_policy import AccessDecision, LogicalDataAccessPolicy
from app.services.trino_readonly import TrinoReadonlyService


VERIFICATION_CONFIRMED = "VERIFICATION_CONFIRMED"
VERIFICATION_PENDING = "VERIFICATION_PENDING"
VERIFICATION_INCONCLUSIVE = "VERIFICATION_INCONCLUSIVE"
VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
VERIFICATION_ERROR = "VERIFICATION_ERROR"
RUNTIME_DRIFT = "RUNTIME_DRIFT"


@dataclass(frozen=True, slots=True)
class VerificationObservation:
    status: str
    details: dict[str, Any]


class TrinoRuntimeVerificationService:
    """Verify one synchronized Ranger projection through a configured Trino user."""

    def __init__(
        self,
        settings: Settings,
        *,
        trino: TrinoReadonlyService | None = None,
    ) -> None:
        self.settings = settings
        self.trino = trino or TrinoReadonlyService(settings)

    def verify(
        self,
        *,
        projection_type: str,
        projection_key: str,
        logical_policy: LogicalDataAccessPolicy,
        ranger_apply_timestamp: datetime | None,
    ) -> VerificationObservation:
        if not self._persona_is_direct_policy_user(logical_policy):
            return VerificationObservation(
                VERIFICATION_UNAVAILABLE,
                {
                    "reason": "configured Trino verification user is not a direct USER subject",
                    "verification_user": self.settings.trino_readonly_user,
                },
            )

        if projection_type == "ACCESS":
            return self._verify_access(
                logical_policy=logical_policy,
                ranger_apply_timestamp=ranger_apply_timestamp,
            )
        if projection_type == "ROW_FILTER":
            return self._verify_row_filter(
                logical_policy=logical_policy,
                ranger_apply_timestamp=ranger_apply_timestamp,
            )
        if projection_type == "MASK":
            return VerificationObservation(
                VERIFICATION_UNAVAILABLE,
                {
                    "reason": (
                        "MASK verification requires a control/baseline persona or "
                        "known unmasked fixture; neither is configured"
                    ),
                    "projection_key": projection_key,
                },
            )
        return VerificationObservation(
            VERIFICATION_UNAVAILABLE,
            {"reason": f"unsupported projection type {projection_type!r}"},
        )

    def _verify_access(
        self,
        *,
        logical_policy: LogicalDataAccessPolicy,
        ranger_apply_timestamp: datetime | None,
    ) -> VerificationObservation:
        decision = logical_policy.access.get("select")
        if decision is None:
            return VerificationObservation(
                VERIFICATION_UNAVAILABLE,
                {"reason": "ACCESS projection has no SELECT decision"},
            )

        sql = (
            f"SELECT 1 FROM {self._qualified_table(logical_policy)} "
            "LIMIT 1"
        )
        try:
            result = self.trino.query(sql=sql)
        except ExternalSystemError as exc:
            error_name = str(exc.details.get("error_name") or "").upper()
            if error_name == "PERMISSION_DENIED":
                observed_allowed = False
                evidence = {
                    "query_kind": "SELECT_PROBE",
                    "observed_allowed": False,
                    "error_name": error_name,
                }
            else:
                return VerificationObservation(
                    VERIFICATION_ERROR,
                    {
                        "reason": "Trino query failed without permission-denied evidence",
                        "error_name": error_name or None,
                        "error_type": exc.details.get("error_type"),
                        "retryable": exc.retryable,
                    },
                )
        except ValidationError as exc:
            return VerificationObservation(
                VERIFICATION_UNAVAILABLE,
                {"reason": exc.message},
            )
        else:
            observed_allowed = True
            evidence = {
                "query_kind": "SELECT_PROBE",
                "observed_allowed": True,
                "row_count_returned": result.get("row_count_returned"),
                "query_id": result.get("query_id"),
            }

        expected_allowed = decision == AccessDecision.ALLOW
        if observed_allowed == expected_allowed:
            return VerificationObservation(
                VERIFICATION_CONFIRMED,
                {**evidence, "expected_allowed": expected_allowed},
            )

        return VerificationObservation(
            self._negative_status(ranger_apply_timestamp),
            {**evidence, "expected_allowed": expected_allowed},
        )

    def _verify_row_filter(
        self,
        *,
        logical_policy: LogicalDataAccessPolicy,
        ranger_apply_timestamp: datetime | None,
    ) -> VerificationObservation:
        expression = logical_policy.row_filter
        if not expression:
            return VerificationObservation(
                VERIFICATION_UNAVAILABLE,
                {"reason": "ROW_FILTER projection has no expression"},
            )

        # If Ranger enforces the configured row filter, adding NOT(expression)
        # to the user's query cannot return a row. A positive count is direct
        # contradiction evidence. A zero count alone is not proof because the
        # underlying dataset may naturally contain no violating rows.
        sql = (
            "SELECT count(*) AS violations FROM "
            f"{self._qualified_table(logical_policy)} "
            f"WHERE NOT ({expression})"
        )
        try:
            result = self.trino.query(sql=sql)
        except (ExternalSystemError, ValidationError) as exc:
            details = getattr(exc, "details", {}) or {}
            return VerificationObservation(
                VERIFICATION_ERROR
                if isinstance(exc, ExternalSystemError)
                else VERIFICATION_UNAVAILABLE,
                {
                    "reason": getattr(exc, "message", str(exc)),
                    "error_name": details.get("error_name"),
                    "error_type": details.get("error_type"),
                },
            )

        violations = self._scalar_int(result)
        evidence = {
            "query_kind": "ROW_FILTER_VIOLATION_PROBE",
            "violations": violations,
            "query_id": result.get("query_id"),
        }
        if violations > 0:
            return VerificationObservation(
                self._negative_status(ranger_apply_timestamp),
                evidence,
            )
        return VerificationObservation(
            VERIFICATION_INCONCLUSIVE,
            {
                **evidence,
                "reason": (
                    "no violating row observed, but no unfiltered baseline/control "
                    "persona exists to prove the filter changed visibility"
                ),
            },
        )

    def _negative_status(self, ranger_apply_timestamp: datetime | None) -> str:
        if ranger_apply_timestamp is None:
            return VERIFICATION_PENDING
        observed_at = utcnow()
        applied_at = ranger_apply_timestamp
        if applied_at.tzinfo is None:
            applied_at = applied_at.replace(tzinfo=UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        elapsed = (observed_at - applied_at).total_seconds()
        return (
            VERIFICATION_PENDING
            if elapsed <= self.settings.eventual_consistency_window_seconds
            else RUNTIME_DRIFT
        )

    def _persona_is_direct_policy_user(
        self,
        logical_policy: LogicalDataAccessPolicy,
    ) -> bool:
        user = str(self.settings.trino_readonly_user or "").strip()
        if not user:
            return False
        return any(
            subject.type.value == "USER" and subject.name == user
            for subject in logical_policy.subjects
        )

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def _qualified_table(self, logical_policy: LogicalDataAccessPolicy) -> str:
        resource = logical_policy.resource
        return ".".join(
            self._quote_identifier(value)
            for value in (
                resource.catalog,
                resource.schema_name,
                resource.table,
            )
        )

    @staticmethod
    def _scalar_int(result: dict[str, Any]) -> int:
        rows = result.get("rows") or []
        if not rows or not isinstance(rows[0], list) or not rows[0]:
            raise ValidationError("Trino verification query returned no scalar result")
        value = rows[0][0]
        if isinstance(value, bool):
            raise ValidationError("Trino verification scalar must be numeric")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Trino verification scalar must be an integer"
            ) from exc
