"""Runtime verification of Ranger policy effects through Trino.

Verification is conservative and evidence-based:
- ACCESS uses the configured policy verification USER directly.
- MASK and ROW_FILTER additionally require an independent control USER that is
  expected to see the unmasked/unfiltered table.
- unsupported identities, empty control evidence, and infrastructure failures
  never become RUNTIME_DRIFT.

MASK verification follows the Apache Ranger Trino MASK_HASH transformer:
to_hex(sha256(to_utf8({col}))).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.models.job import utcnow
from app.schemas.data_access_policy import (
    AccessDecision,
    LogicalDataAccessPolicy,
    LogicalMaskIntent,
    SubjectType,
)
from app.services.trino_readonly import TrinoReadonlyService

ExpectedObservation = Literal[
    "QUERY_SUCCESS",
    "ACCESS_DENIED",
    "MASK_HASH_MATCH",
    "ROW_FILTER_ENFORCED",
]


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    supported: bool
    projection_type: str
    persona: str | None
    sql: str | None
    expected: ExpectedObservation | None
    reason: str | None = None
    control_persona: str | None = None
    control_sql: str | None = None


def _quoted_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _table_sql(policy: LogicalDataAccessPolicy) -> str:
    resource = policy.resource
    return ".".join(
        _quoted_identifier(part)
        for part in (resource.catalog, resource.schema_name, resource.table)
    )


def _probe_sql(policy: LogicalDataAccessPolicy) -> str:
    return f"SELECT 1 AS verification_probe FROM {_table_sql(policy)} LIMIT 1"


def _mask_projection_key(column: str) -> str:
    return f"hash:{hashlib.sha256(column.encode()).hexdigest()[:12]}"


def _mask_column(
    policy: LogicalDataAccessPolicy,
    projection_key: str | None,
) -> str | None:
    if not projection_key:
        return None
    for column, intent in policy.masks.items():
        if (
            intent == LogicalMaskIntent.MASK
            and _mask_projection_key(column) == projection_key
        ):
            return column
    return None


def _mask_subject_sql(
    policy: LogicalDataAccessPolicy,
    column: str,
    sample_rows: int,
) -> str:
    table = _table_sql(policy)
    col = _quoted_identifier(column)
    return (
        f"SELECT DISTINCT CAST({col} AS varchar) AS verification_value "
        f"FROM {table} WHERE {col} IS NOT NULL "
        f"ORDER BY verification_value LIMIT {sample_rows}"
    )


def _mask_control_sql(
    policy: LogicalDataAccessPolicy,
    column: str,
    sample_rows: int,
) -> str:
    table = _table_sql(policy)
    col = _quoted_identifier(column)
    return (
        "SELECT DISTINCT "
        f"typeof({col}) AS source_type, "
        f"to_hex(sha256(to_utf8(CAST({col} AS varchar)))) "
        "AS verification_value "
        f"FROM {table} WHERE {col} IS NOT NULL "
        f"ORDER BY verification_value LIMIT {sample_rows}"
    )


def _row_filter_sql(policy: LogicalDataAccessPolicy) -> str:
    expression = policy.row_filter or ""
    return (
        "SELECT count(*) AS verification_violations "
        f"FROM {_table_sql(policy)} WHERE NOT ({expression})"
    )


def build_verification_plan(
    *,
    logical_policy: LogicalDataAccessPolicy,
    projection_type: str,
    verification_user: str | None,
    projection_key: str | None = None,
    control_user: str | None = None,
    sample_rows: int = 20,
) -> VerificationPlan:
    persona = (verification_user or "").strip() or None
    control_persona = (control_user or "").strip() or None

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

    if projection_type == "ACCESS":
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

    if control_persona is None:
        return VerificationPlan(
            supported=False,
            projection_type=projection_type,
            persona=persona,
            sql=None,
            expected=None,
            reason=(
                "TRINO_VERIFICATION_CONTROL_USER is required for "
                f"{projection_type} verification"
            ),
        )
    if control_persona == persona:
        return VerificationPlan(
            supported=False,
            projection_type=projection_type,
            persona=persona,
            sql=None,
            expected=None,
            reason="verification control user must differ from policy verification user",
        )

    if projection_type == "MASK":
        column = _mask_column(logical_policy, projection_key)
        if column is None:
            return VerificationPlan(
                supported=False,
                projection_type=projection_type,
                persona=persona,
                sql=None,
                expected=None,
                reason="MASK projection key does not resolve to one logical mask column",
                control_persona=control_persona,
            )
        return VerificationPlan(
            supported=True,
            projection_type=projection_type,
            persona=persona,
            sql=_mask_subject_sql(logical_policy, column, sample_rows),
            expected="MASK_HASH_MATCH",
            control_persona=control_persona,
            control_sql=_mask_control_sql(logical_policy, column, sample_rows),
        )

    if projection_type == "ROW_FILTER":
        if logical_policy.row_filter is None:
            return VerificationPlan(
                supported=False,
                projection_type=projection_type,
                persona=persona,
                sql=None,
                expected=None,
                reason="ROW_FILTER projection has no logical row_filter",
                control_persona=control_persona,
            )
        sql = _row_filter_sql(logical_policy)
        return VerificationPlan(
            supported=True,
            projection_type=projection_type,
            persona=persona,
            sql=sql,
            expected="ROW_FILTER_ENFORCED",
            control_persona=control_persona,
            control_sql=sql,
        )

    return VerificationPlan(
        supported=False,
        projection_type=projection_type,
        persona=persona,
        sql=None,
        expected=None,
        reason=f"unsupported projection type {projection_type!r}",
        control_persona=control_persona,
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


def _first_column_values(result: dict[str, Any]) -> list[str]:
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return []
    values: list[str] = []
    for row in rows:
        if isinstance(row, list) and row:
            values.append(str(row[0]))
    return values


def _mask_control_evidence(
    result: dict[str, Any],
) -> tuple[str | None, list[str]]:
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return None, []
    source_type: str | None = None
    values: list[str] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        observed_type = str(row[0]).strip().lower()
        if source_type is None:
            source_type = observed_type
        elif source_type != observed_type:
            return None, []
        values.append(str(row[1]))
    return source_type, values


def _mask_type_is_character(source_type: str | None) -> bool:
    if not source_type:
        return False
    normalized = source_type.lower().replace(" ", "")
    return (
        normalized == "varchar"
        or normalized.startswith("varchar(")
        or normalized == "char"
        or normalized.startswith("char(")
    )


def _scalar_int(result: dict[str, Any]) -> int | None:
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, list) or not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


class PolicyRuntimeVerificationService:
    def __init__(
        self,
        settings: Settings,
        *,
        trino_service: TrinoReadonlyService | None = None,
        control_trino_service: TrinoReadonlyService | None = None,
    ) -> None:
        self.settings = settings
        self.trino = trino_service or TrinoReadonlyService(settings)
        if control_trino_service is not None:
            self.control_trino = control_trino_service
        elif settings.trino_verification_control_user:
            control_settings = settings.model_copy(
                update={
                    "trino_readonly_user": settings.trino_verification_control_user,
                    "trino_readonly_password": (
                        settings.trino_verification_control_password
                    ),
                }
            )
            self.control_trino = TrinoReadonlyService(control_settings)
        else:
            self.control_trino = None

    def verify(
        self,
        *,
        logical_policy: LogicalDataAccessPolicy,
        projection_type: str,
        ranger_apply_timestamp,
        projection_key: str | None = None,
    ) -> dict[str, Any]:
        plan = build_verification_plan(
            logical_policy=logical_policy,
            projection_type=projection_type,
            projection_key=projection_key,
            verification_user=self.settings.trino_readonly_user,
            control_user=self.settings.trino_verification_control_user,
            sample_rows=self.settings.trino_verification_sample_rows,
        )
        base = {
            "projection_type": projection_type,
            "projection_key": projection_key,
            "persona": plan.persona,
            "control_persona": plan.control_persona,
            "expected": plan.expected,
            "sql": plan.sql,
            "control_sql": plan.control_sql,
            "verified_at": utcnow().isoformat(),
        }
        if not plan.supported:
            return {
                **base,
                "status": "VERIFICATION_UNAVAILABLE",
                "reason": plan.reason,
            }

        if projection_type == "ACCESS":
            return self._verify_access(
                plan=plan,
                base=base,
                ranger_apply_timestamp=ranger_apply_timestamp,
            )

        if self.control_trino is None:
            return {
                **base,
                "status": "VERIFICATION_UNAVAILABLE",
                "reason": "configured verification plan requires a control Trino identity",
            }

        try:
            subject_result = self.trino.query(sql=plan.sql or "")
            control_result = self.control_trino.query(sql=plan.control_sql or "")
        except ExternalSystemError as exc:
            return {
                **base,
                "status": "VERIFICATION_ERROR",
                "reason": exc.message,
                "retryable": bool(exc.retryable),
                "error_details": exc.details,
            }

        if projection_type == "MASK":
            observed_values = _first_column_values(subject_result)
            source_type, expected_values = _mask_control_evidence(control_result)
            if not expected_values:
                return {
                    **base,
                    "status": "VERIFICATION_UNAVAILABLE",
                    "reason": "control identity returned no non-null mask sample values",
                }
            if not _mask_type_is_character(source_type):
                return {
                    **base,
                    "status": "VERIFICATION_UNAVAILABLE",
                    "reason": (
                        "MASK_HASH runtime verification is currently deterministic "
                        "only for character columns"
                    ),
                    "observed": {"source_type": source_type},
                }
            matches = observed_values == expected_values
            observed: Any = {
                "source_type": source_type,
                "subject_values": observed_values,
                "control_expected_values": expected_values,
            }
            query_ids = {
                "subject": subject_result.get("query_id"),
                "control": control_result.get("query_id"),
            }
        elif projection_type == "ROW_FILTER":
            subject_violations = _scalar_int(subject_result)
            control_violations = _scalar_int(control_result)
            if subject_violations is None or control_violations is None:
                return {
                    **base,
                    "status": "VERIFICATION_ERROR",
                    "reason": "row-filter verification query returned an invalid count",
                    "retryable": False,
                }
            if control_violations <= 0:
                return {
                    **base,
                    "status": "VERIFICATION_UNAVAILABLE",
                    "reason": (
                        "control identity sees no rows outside the row_filter; "
                        "enforcement cannot be distinguished from source data"
                    ),
                    "observed": {
                        "subject_violations": subject_violations,
                        "control_violations": control_violations,
                    },
                }
            matches = subject_violations == 0
            observed = {
                "subject_violations": subject_violations,
                "control_violations": control_violations,
            }
            query_ids = {
                "subject": subject_result.get("query_id"),
                "control": control_result.get("query_id"),
            }
        else:
            return {
                **base,
                "status": "VERIFICATION_UNAVAILABLE",
                "reason": f"unsupported projection type {projection_type!r}",
            }

        status, elapsed_seconds = self._status_from_match(
            matches=matches,
            ranger_apply_timestamp=ranger_apply_timestamp,
        )
        return {
            **base,
            "status": status,
            "observed": observed,
            "matches_expected": matches,
            "query_id": query_ids,
            "elapsed_seconds": elapsed_seconds,
            "eventual_consistency_window_seconds": (
                self.settings.eventual_consistency_window_seconds
            ),
        }

    def _verify_access(
        self,
        *,
        plan: VerificationPlan,
        base: dict[str, Any],
        ranger_apply_timestamp,
    ) -> dict[str, Any]:
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
        status, elapsed_seconds = self._status_from_match(
            matches=matches,
            ranger_apply_timestamp=ranger_apply_timestamp,
        )
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

    def _status_from_match(
        self,
        *,
        matches: bool,
        ranger_apply_timestamp,
    ) -> tuple[str, float]:
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
        return status, elapsed_seconds


__all__ = [
    "PolicyRuntimeVerificationService",
    "VerificationPlan",
    "build_verification_plan",
]
