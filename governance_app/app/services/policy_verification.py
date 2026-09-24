"""Runtime verification of Ranger policy effects through Trino.

This module is intentionally conservative. A projection is verified only when
the configured Trino verification identity can actually exercise the exact
policy subject and the expected observation is deterministic.

Currently supported:
- ACCESS/select ALLOW for the configured verification USER.
- ACCESS/select DENY for the configured verification USER.

MASK and ROW_FILTER require a control/baseline observation contract before a
runtime result can be compared safely, so they remain explicitly unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.models.job import utcnow
from app.schemas.data_access_policy import (
    AccessDecision,
    LogicalDataAccessPolicy,
    SubjectType,
)
from app.services.trino_readonly import TrinoReadonlyService

ExpectedObservation = Literal["QUERY_SUCCESS", "ACCESS_DENIED"]


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    supported: bool
    projection_type: str
    persona: str | None
    sql: str | None
    expected: ExpectedObservation | None
    reason: str | None = None


def _quoted_identifier(value: str) -> str:
    """Quote one Trino identifier without treating policy text as SQL."""
    return '"' + str(value).replace('"', '""') + '"'


def _probe_sql(policy: LogicalDataAccessPolicy) -> str:
    resource = policy.resource
    table = ".".join(
        _quoted_identifier(part)
        for part in (resource.catalog, resource.schema_name, resource.table)
    )
    return f"SELECT 1 AS verification_probe FROM {table} LIMIT 1"


def build_verification_plan(
    *,
    logical_policy: LogicalDataAccessPolicy,
    projection_type: str,
    verification_user: str | None,
) -> VerificationPlan:
    persona = (verification_user or "").strip() or None
    if persona is None:
        return VerificationPlan(
            supported=False,
            projection_type=projection_type,
            persona=None,
            sql=None,
            expected=None,
            reason="TRINO_READONLY_USER is not configured",
        )

    direct_users = {
        subject.name
        for subject in logical_policy.subjects
        if subject.type == SubjectType.USER
    }
    if persona not in direct_users:
        return VerificationPlan(
            supported=False,
            projection_type=projection_type,
            persona=persona,
            sql=None,
            expected=None,
            reason=(
                "configured Trino verification user is not a direct USER "
                "subject of this policy"
            ),
        )

    if projection_type != "ACCESS":
        reason = (
            "MASK verification requires a baseline/control observation"
            if projection_type == "MASK"
            else "ROW_FILTER verification requires an expected-row baseline"
            if projection_type == "ROW_FILTER"
            else f"unsupported projection type {projection_type!r}"
        )
        return VerificationPlan(
            supported=False,
            projection_type=projection_type,
            persona=persona,
            sql=None,
            expected=None,
            reason=reason,
        )

    select_decision = logical_policy.access.get("select")
    if select_decision is None:
        return VerificationPlan(
            supported=False,
            projection_type=projection_type,
            persona=persona,
            sql=None,
            expected=None,
            reason="ACCESS projection has no select decision to verify read-only",
        )

    expected: ExpectedObservation = (
        "QUERY_SUCCESS"
        if select_decision == AccessDecision.ALLOW
        else "ACCESS_DENIED"
    )
    return VerificationPlan(
        supported=True,
        projection_type=projection_type,
        persona=persona,
        sql=_probe_sql(logical_policy),
        expected=expected,
    )


def _is_access_denied(exc: ExternalSystemError) -> bool:
    details = exc.details or {}
    values = [
        str(details.get("error_name") or ""),
        str(details.get("error_type") or ""),
        str(details.get("exception_type") or ""),
        str(exc.message or ""),
    ]
    normalized = " ".join(values).upper()
    return any(
        marker in normalized
        for marker in (
            "ACCESS_DENIED",
            "PERMISSION_DENIED",
            "PERMISSION DENIED",
            "NOT AUTHORIZED",
            "NOT AUTHORISED",
            "ACCESS CONTROL",
        )
    )


class PolicyRuntimeVerificationService:
    def __init__(
        self,
        settings: Settings,
        *,
        trino_service: TrinoReadonlyService | None = None,
    ) -> None:
        self.settings = settings
        self.trino = trino_service or TrinoReadonlyService(settings)

    def verify(
        self,
        *,
        logical_policy: LogicalDataAccessPolicy,
        projection_type: str,
        ranger_apply_timestamp,
    ) -> dict[str, Any]:
        plan = build_verification_plan(
            logical_policy=logical_policy,
            projection_type=projection_type,
            verification_user=self.settings.trino_readonly_user,
        )
        base = {
            "projection_type": projection_type,
            "persona": plan.persona,
            "expected": plan.expected,
            "sql": plan.sql,
            "verified_at": utcnow().isoformat(),
        }
        if not plan.supported:
            return {
                **base,
                "status": "VERIFICATION_UNAVAILABLE",
                "reason": plan.reason,
            }

        observed: str
        query_id: str | None = None
        try:
            result = self.trino.query(sql=plan.sql or "")
            observed = "QUERY_SUCCESS"
            query_id = (
                str(result.get("query_id"))
                if isinstance(result, dict) and result.get("query_id")
                else None
            )
        except ExternalSystemError as exc:
            if _is_access_denied(exc):
                observed = "ACCESS_DENIED"
            else:
                return {
                    **base,
                    "status": "VERIFICATION_ERROR",
                    "reason": exc.message,
                    "retryable": bool(exc.retryable),
                    "error_details": exc.details,
                }

        matches = observed == plan.expected
        elapsed_seconds = max(
            0.0,
            (utcnow() - ranger_apply_timestamp).total_seconds(),
        )
        if matches:
            status = "VERIFICATION_CONFIRMED"
        elif elapsed_seconds <= self.settings.eventual_consistency_window_seconds:
            status = "VERIFICATION_PENDING"
        else:
            status = "RUNTIME_DRIFT"

        return {
            **base,
            "status": status,
            "observed": observed,
            "matches_expected": matches,
            "query_id": query_id,
            "elapsed_seconds": elapsed_seconds,
            "eventual_consistency_window_seconds": (
                self.settings.eventual_consistency_window_seconds
            ),
        }


__all__ = [
    "PolicyRuntimeVerificationService",
    "VerificationPlan",
    "build_verification_plan",
]
