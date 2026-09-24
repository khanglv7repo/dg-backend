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
    ExternalSystemError,
    GovernanceError,
)
from app.db.session import SessionLocal
from app.services.audit_query import AuditQueryService
from app.services.data_access_policy import DataAccessPolicyService
from app.services.policy_query import PolicyQueryService
from app.services.ranger_client_factory import build_resource_ranger_client
from app.services.ranger_inspection import (
    RangerInspectionService,
    create_ranger_tag_store_client,
)
from app.services.service_mapping import ServiceMappingService
from app.services.tag_sync_observability import TagSyncObservabilityService
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
def get_tag_sync_observability(
    entity_type: str,
    entity_fqn: str,
) -> dict[str, Any]:
    """Read bounded webhook, TAG_SYNC, and Ranger tag convergence evidence for one entity."""

    tag_store = None
    try:
        settings = get_settings()
        tag_store = create_ranger_tag_store_client(settings)
        with SessionLocal() as db:
            return _result(
                TagSyncObservabilityService(db, tag_store=tag_store).inspect(
                    entity_type=entity_type.strip(),
                    entity_fqn=entity_fqn.strip(),
                )
            )
    except GovernanceError as exc:
        raise _tool_error(exc) from None
    except Exception:
        raise _internal_tool_error() from None
    finally:
        if tag_store is not None:
            tag_store.close()


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
