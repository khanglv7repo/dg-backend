from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app.core.config import Settings, get_settings
from app.core.errors import (
    ConfigurationError,
    ControlConfirmationError,
    ExternalSystemError,
    GovernanceError,
)
from app.db.session import SessionLocal
from app.services.audit_query import AuditQueryService
from app.services.classification_completion import ClassificationCompletionService
from app.services.data_access_policy import DataAccessPolicyService
from app.services.policy_lifecycle import PolicyLifecycleService
from app.services.policy_query import PolicyQueryService
from app.services.ranger_client_factory import build_resource_ranger_client
from app.services.ranger_inspection import RangerInspectionService
from app.services.service_mapping import ServiceMappingService
from app.services.trino_readonly import TrinoReadonlyService
from app.services.workflow_query import WorkflowQueryService

mcp = FastMCP("Data Governance Backend MCP", mask_error_details=True)

_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "database_url",
    "credential",
)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _safe(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return value


def _error_payload(exc: GovernanceError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "retryable": bool(getattr(exc, "retryable", False)),
            "details": _safe(exc.details),
        },
    }
    if isinstance(exc, ExternalSystemError):
        payload["error"]["system"] = exc.system
        if exc.status_code is not None:
            payload["error"]["status_code"] = exc.status_code
    return payload


def _tool_error(exc: GovernanceError) -> ToolError:
    return ToolError(json.dumps(_error_payload(exc), ensure_ascii=False, default=str))


def _internal_tool_error() -> ToolError:
    payload = {
        "ok": False,
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "internal MCP tool failure",
            "retryable": False,
        },
    }
    return ToolError(json.dumps(payload, separators=(",", ":")))


def _result(value: Any) -> Any:
    return _safe(jsonable_encoder(value))


def _confirmation(confirmed: bool, action: str) -> None:
    if confirmed is not True:
        raise ControlConfirmationError(
            f"{action} requires confirmed=true before changing Backend authority",
            details={
                "action": action,
                "confirmed": False,
                "note": "confirmation is a workflow guard, not authentication",
            },
        )


def _actor(settings: Settings) -> tuple[str, str]:
    return settings.mcp_actor_id, settings.mcp_actor_name


@mcp.tool
def get_policy(policy_key: str, version: int | None = None) -> dict[str, Any]:
    """Return authoritative Backend logical policy state, never native Ranger truth."""

    try:
        settings = get_settings()
        with SessionLocal() as db:
            return _result(
                PolicyQueryService(db, settings).get_policy(
                    policy_key=policy_key,
                    version=version,
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def list_policy_versions(policy_key: str) -> list[dict[str, Any]]:
    """List immutable Backend policy versions in version order."""

    try:
        settings = get_settings()
        with SessionLocal() as db:
            return _result(
                PolicyQueryService(db, settings).list_policy_versions(
                    policy_key=policy_key
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def preview_policy_change(
    policy_key: str,
    logical_policy: dict[str, Any],
) -> dict[str, Any]:
    """Run the R4 side-effect-free logical/Ranger preview path."""

    ranger = None
    try:
        settings = get_settings()
        ranger = build_resource_ranger_client(settings)
        with SessionLocal() as db:
            preview = DataAccessPolicyService(
                db,
                settings,
                ranger_client=ranger,
            ).preview(
                policy_key=policy_key,
                logical_policy=logical_policy,
            )
            return _result(preview)
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None
    finally:
        if ranger is not None:
            ranger.close()


@mcp.tool
def check_policy_conflict(
    policy_key: str,
    logical_policy: dict[str, Any],
) -> dict[str, Any]:
    """Check bounded exact resource/subject policy overlaps without AI semantics."""

    try:
        settings = get_settings()
        with SessionLocal() as db:
            return _result(
                PolicyQueryService(db, settings).check_policy_conflict(
                    policy_key=policy_key,
                    logical_policy=logical_policy,
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def resolve_resource_mapping(
    om_service_name: str,
    environment: str,
) -> dict[str, Any]:
    """Resolve one exact Backend service mapping; no fuzzy inference."""

    try:
        with SessionLocal() as db:
            return _result(
                ServiceMappingService(db).resolve(
                    om_service_name=om_service_name,
                    environment=environment,
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def get_ranger_sync_status(
    policy_key: str,
    version: int | None = None,
) -> dict[str, Any]:
    """Return durable Backend Ranger projection/reconciliation state."""

    try:
        settings = get_settings()
        with SessionLocal() as db:
            return _result(
                PolicyQueryService(db, settings).get_ranger_sync_status(
                    policy_key=policy_key,
                    version=version,
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def get_workflow_status(execution_id: str) -> dict[str, Any]:
    """Read bounded durable workflow/execution status from existing Backend state."""

    try:
        with SessionLocal() as db:
            return _result(WorkflowQueryService(db).get(execution_id))
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def get_audit_summary(
    object_type: str | None = None,
    object_id: str | None = None,
    policy_key: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return bounded existing audit records with a hard server-side limit."""

    try:
        with SessionLocal() as db:
            return _result(
                AuditQueryService(db).summary(
                    object_type=object_type,
                    object_id=object_id,
                    policy_key=policy_key,
                    action=action,
                    since=since,
                    until=until,
                    limit=limit,
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def inspect_ranger_state(
    kind: Literal["health", "policy", "policy_key", "user", "group"],
    name: str | None = None,
    policy_key: str | None = None,
) -> dict[str, Any]:
    """Read bounded Ranger diagnostics; never create, update, or delete Ranger state."""

    ranger = None
    try:
        settings = get_settings()
        ranger = build_resource_ranger_client(settings)
        with SessionLocal() as db:
            return _result(
                RangerInspectionService(
                    db,
                    ranger_client=ranger,
                ).inspect(
                    kind=kind,
                    name=name,
                    policy_key=policy_key,
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None
    finally:
        if ranger is not None:
            ranger.close()


@mcp.tool
def query_trino_readonly(sql: str) -> dict[str, Any]:
    """Execute one bounded diagnostic read query under configured read-only Trino identity."""

    try:
        settings = get_settings()
        return _result(TrinoReadonlyService(settings).query(sql=sql))
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def create_policy_version(
    policy_key: str,
    logical_policy: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    """Create an immutable proposal/DRAFT only; never activates or dispatches Ranger sync."""

    try:
        settings = get_settings()
        actor_id, actor_name = _actor(settings)
        with SessionLocal() as db:
            with db.begin():
                version = DataAccessPolicyService(db, settings).create_version(
                    policy_key=policy_key,
                    logical_policy=logical_policy,
                    actor_id=actor_id,
                    actor_name=actor_name,
                )
            result = PolicyQueryService(db, settings).get_policy(
                policy_key=policy_key,
                version=version.version,
            )
            result["authority_changed"] = False
            result["dispatched"] = False
            if reason:
                result["request_reason"] = reason.strip()[:1000]
            return _result(result)
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def activate_policy_version(
    policy_key: str,
    version: int,
    confirmed: bool = False,
    approval_reason: str | None = None,
) -> dict[str, Any]:
    """Activate one immutable R4 version after explicit MCP workflow confirmation."""

    ranger = None
    try:
        _confirmation(confirmed, "activate_policy_version")
        settings = get_settings()
        actor_id, actor_name = _actor(settings)
        ranger = build_resource_ranger_client(settings)
        with SessionLocal() as db:
            result = PolicyLifecycleService(
                db,
                settings,
                ranger_client=ranger,
            ).activate(
                policy_key=policy_key,
                version=version,
                actor_id=actor_id,
                actor_name=actor_name,
            )
            response = PolicyQueryService(db, settings).get_policy(
                policy_key=policy_key,
                version=result.version.version,
            )
            response.update(
                {
                    "authority_changed": result.authority_changed,
                    "dispatched": result.dispatched,
                    "task_id": result.task_id,
                }
            )
            if approval_reason:
                response["approval_reason"] = approval_reason.strip()[:1000]
            return _result(response)
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None
    finally:
        if ranger is not None:
            ranger.close()


@mcp.tool
def rollback_policy(
    policy_key: str,
    target_version: int,
    confirmed: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Reactivate the exact immutable target version; never infer a previous version."""

    ranger = None
    try:
        _confirmation(confirmed, "rollback_policy")
        settings = get_settings()
        actor_id, actor_name = _actor(settings)
        ranger = build_resource_ranger_client(settings)
        with SessionLocal() as db:
            result = PolicyLifecycleService(
                db,
                settings,
                ranger_client=ranger,
            ).rollback(
                policy_key=policy_key,
                target_version=target_version,
                actor_id=actor_id,
                actor_name=actor_name,
            )
            response = PolicyQueryService(db, settings).get_policy(
                policy_key=policy_key,
                version=result.version.version,
            )
            response.update(
                {
                    "authority_changed": result.authority_changed,
                    "dispatched": result.dispatched,
                    "task_id": result.task_id,
                }
            )
            if reason:
                response["reason"] = reason.strip()[:1000]
            return _result(response)
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None
    finally:
        if ranger is not None:
            ranger.close()


@mcp.tool
def update_service_mapping(
    om_service_name: str,
    trino_catalog: str,
    ranger_service_name: str,
    environment: str,
    confirmed: bool = False,
    ranger_tag_service_name: str | None = None,
    enabled: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    """Persist one explicit Backend mapping after confirmation; no fuzzy inference/Ranger write."""

    try:
        _confirmation(confirmed, "update_service_mapping")
        settings = get_settings()
        actor_id, actor_name = _actor(settings)
        with SessionLocal() as db:
            with db.begin():
                mapping = ServiceMappingService(db).update(
                    om_service_name=om_service_name,
                    trino_catalog=trino_catalog,
                    ranger_service_name=ranger_service_name,
                    ranger_tag_service_name=ranger_tag_service_name,
                    environment=environment,
                    enabled=enabled,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    reason=reason,
                )
            mapping["authority_changed"] = True
            mapping["ranger_mutation"] = False
            mapping["reconciliation_enqueued"] = False
            return _result(mapping)
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def request_ranger_sync(policy_key: str) -> dict[str, Any]:
    """Republish existing R4 reconciliation for the current ACTIVE policy only."""

    try:
        settings = get_settings()
        with SessionLocal() as db:
            return _result(
                PolicyLifecycleService(db, settings).request_sync(
                    policy_key=policy_key
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


@mcp.tool
def complete_classification_execution(
    execution_id: str,
    generation: int,
    status: Literal["COMPLETED", "NO_PROPOSAL"],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Generation-fenced completion of one already-dispatched WAITING_AI execution.

    This extends the frozen R5 MCP contract for R6-B. It does not create new
    governance intent and therefore does not require confirmed=true.
    """

    try:
        settings = get_settings()
        actor_id, actor_name = _actor(settings)
        with SessionLocal() as db:
            with db.begin():
                response = ClassificationCompletionService(db).complete(
                    execution_id=execution_id,
                    generation=generation,
                    status=status,
                    result=result,
                    actor_id=actor_id,
                    actor_name=actor_name,
                )
            return _result(response)
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None


def run() -> None:
    settings = get_settings()
    if not settings.mcp_enabled:
        raise ConfigurationError("Backend MCP is disabled; set MCP_ENABLED=true")
    mcp.run(
        transport="http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path=settings.mcp_path,
    )


if __name__ == "__main__":
    run()
