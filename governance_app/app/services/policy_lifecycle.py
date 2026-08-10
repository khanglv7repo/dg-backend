from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.core.errors import ConfigurationError, ExternalSystemError, NotFoundError
from app.services.data_access_policy import DataAccessPolicyService
from app.tasks.policy_sync import sync_policy_to_ranger

DispatchFn = Callable[[str, str | None], str | None]


@dataclass(frozen=True)
class PolicyLifecycleResult:
    version: Any
    authority_changed: bool
    dispatched: bool
    task_id: str | None


def dispatch_policy_sync(policy_version_id: str, correlation_id: str | None = None) -> str | None:
    """Publish existing R4 reconciliation only after durable authority is committed."""

    try:
        task = sync_policy_to_ranger.delay(
            policy_version_id=policy_version_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        raise ExternalSystemError(
            "policy authority is durable but Celery reconciliation publish failed; "
            "retry the same ACTIVE version or request sync",
            system="celery",
            retryable=True,
            details={"policy_version_id": policy_version_id},
        ) from exc
    return str(task.id) if getattr(task, "id", None) else None


class PolicyLifecycleService:
    """Shared REST/MCP orchestration around the accepted R4 lifecycle methods."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        ranger_client: RangerClient | None = None,
        dispatcher: DispatchFn = dispatch_policy_sync,
    ) -> None:
        self.session = session
        self.settings = settings
        self.ranger_client = ranger_client
        self.dispatcher = dispatcher

    def activate(
        self,
        *,
        policy_key: str,
        version: int,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ) -> PolicyLifecycleResult:
        # TX1 is read-only and ends before the Ranger network lookup.
        with self.session.begin():
            target = DataAccessPolicyService(
                self.session,
                self.settings,
            ).read_activation_target(policy_key=policy_key, version=version)

        validation = DataAccessPolicyService(
            self.session,
            self.settings,
            ranger_client=self._ranger(),
        ).validate_activation_subjects(target)

        # TX2 contains only the authoritative desired-state transition.
        with self.session.begin():
            selected, changed = DataAccessPolicyService(
                self.session,
                self.settings,
            ).activate_version(
                validation=validation,
                actor_id=actor_id,
                actor_name=actor_name,
                correlation_id=correlation_id,
            )

        version_id = str(selected.id)
        task_id = self.dispatcher(version_id, correlation_id)
        return PolicyLifecycleResult(
            version=selected,
            authority_changed=bool(changed),
            dispatched=True,
            task_id=task_id,
        )

    def rollback(
        self,
        *,
        policy_key: str,
        target_version: int,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ) -> PolicyLifecycleResult:
        with self.session.begin():
            target = DataAccessPolicyService(
                self.session,
                self.settings,
            ).read_rollback_target(
                policy_key=policy_key,
                target_version=target_version,
            )

        validation = DataAccessPolicyService(
            self.session,
            self.settings,
            ranger_client=self._ranger(),
        ).validate_activation_subjects(target)

        with self.session.begin():
            selected, changed = DataAccessPolicyService(
                self.session,
                self.settings,
            ).rollback(
                policy_key=policy_key,
                target_version=target_version,
                actor_id=actor_id,
                actor_name=actor_name,
                correlation_id=correlation_id,
                validation=validation,
            )

        version_id = str(selected.id)
        task_id = self.dispatcher(version_id, correlation_id)
        return PolicyLifecycleResult(
            version=selected,
            authority_changed=bool(changed),
            dispatched=True,
            task_id=task_id,
        )

    def _ranger(self) -> RangerClient:
        if self.ranger_client is None:
            raise ConfigurationError(
                "Ranger client is required for activation or rollback subject validation"
            )
        return self.ranger_client

    def request_sync(
        self,
        *,
        policy_key: str,
        correlation_id: str | None = None,
    ) -> dict:
        # Technical reconciliation only: no new policy version and no authority write.
        with self.session.begin():
            active, _projections = DataAccessPolicyService(
                self.session,
                self.settings,
            ).status(policy_key=policy_key)
            if active is None:
                raise NotFoundError(f"policy {policy_key!r} has no ACTIVE version")
            version_id = str(active.id)
            version = int(active.version)

        task_id = self.dispatcher(version_id, correlation_id)
        return {
            "policy_key": policy_key,
            "version": version,
            "policy_version_id": version_id,
            "authority_changed": False,
            "dispatched": True,
            "task_id": task_id,
        }
