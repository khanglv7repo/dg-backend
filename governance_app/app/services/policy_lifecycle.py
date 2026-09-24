from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.repositories.event_outbox import EventOutboxRepository
from app.services.data_access_policy import DataAccessPolicyService


@dataclass(frozen=True)
class PolicyLifecycleResult:
    version: Any
    authority_changed: bool
    dispatched: bool
    task_id: str | None



class PolicyLifecycleService:
    """Shared REST/MCP orchestration around the accepted R4 lifecycle methods."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        ranger_client: RangerClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.ranger_client = ranger_client

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
        # EventOutboxRepository.enqueue() is called inside the SAME begin()
        # block so the outbox row commits atomically with the authority write.
        # If Celery is unavailable at dispatch time the dispatcher (Step 3 of the
        # transactional outbox pattern: app/tasks/outbox.py) will drain the row
        # on its next poll. Hard Invariant: both happen or neither does.
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
            if changed:
                EventOutboxRepository(self.session).enqueue(
                    aggregate_type="data_access_policy",
                    aggregate_id=str(selected.policy_key),
                    event_type="policy.version.activated",
                    payload={
                        "policy_key": str(selected.policy_key),
                        "policy_version_id": version_id,
                        "version": int(selected.version),
                        "actor_id": actor_id,
                        "correlation_id": correlation_id,
                    },
                )

        # Transactional Outbox is the single publish path. The API reports
        # durable acceptance here; the dispatcher later flips the Outbox row
        # to DISPATCHED only after Celery accepts sync_policy_to_ranger.
        return PolicyLifecycleResult(
            version=selected,
            authority_changed=bool(changed),
            dispatched=False,
            task_id=None,
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
            # Enqueue outbox row atomically with the rollback authority write.
            if changed:
                EventOutboxRepository(self.session).enqueue(
                    aggregate_type="data_access_policy",
                    aggregate_id=str(selected.policy_key),
                    event_type="policy.version.rolled_back",
                    payload={
                        "policy_key": str(selected.policy_key),
                        "policy_version_id": version_id,
                        "version": int(selected.version),
                        "actor_id": actor_id,
                        "correlation_id": correlation_id,
                    },
                )

        return PolicyLifecycleResult(
            version=selected,
            authority_changed=bool(changed),
            dispatched=False,
            task_id=None,
        )

    def _ranger(self) -> RangerClient:
        if self.ranger_client is None:
            raise ConfigurationError(
                "Ranger client is required for activation or rollback subject validation"
            )
        return self.ranger_client

