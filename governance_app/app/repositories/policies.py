from __future__ import annotations

import uuid
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.policy import GovernancePolicy


class PolicyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[GovernancePolicy]:
        return list(
            self.session.scalars(
                select(GovernancePolicy).order_by(
                    GovernancePolicy.service.asc(),
                    GovernancePolicy.name.asc(),
                )
            )
        )

    def get(self, policy_id: uuid.UUID | str) -> GovernancePolicy:
        identifier = uuid.UUID(str(policy_id))
        policy = self.session.get(GovernancePolicy, identifier)
        if policy is None:
            raise NotFoundError(f"policy {identifier} was not found")
        return policy

    def get_by_key(self, policy_key: str) -> GovernancePolicy | None:
        return self.session.scalar(
            select(GovernancePolicy).where(
                GovernancePolicy.policy_key == policy_key
            )
        )

    def count(self) -> int:
        return len(self.list_all())

    def upsert(
        self,
        *,
        policy_key: str,
        policy_kind: str,
        service: str,
        service_type: str | None,
        name: str,
        document: dict,
        enabled: bool,
    ) -> tuple[GovernancePolicy, bool, bool]:
        existing = self.get_by_key(policy_key)
        conflicting = self.session.scalar(
            select(GovernancePolicy).where(
                GovernancePolicy.service == service,
                GovernancePolicy.name == name,
                GovernancePolicy.policy_key != policy_key,
            )
        )
        if conflicting is not None:
            raise ConflictError(
                f"Ranger policy {service}:{name} is already tracked as "
                f"{conflicting.policy_key}"
            )

        desired_document = deepcopy(document)
        if existing is None:
            policy = GovernancePolicy(
                policy_key=policy_key,
                policy_kind=policy_kind,
                service=service,
                service_type=service_type,
                name=name,
                document=desired_document,
                enabled=enabled,
                revision=1,
            )
            self.session.add(policy)
            self.session.flush()
            return policy, True, True

        changed = any(
            (
                existing.policy_kind != policy_kind,
                existing.service != service,
                existing.service_type != service_type,
                existing.name != name,
                existing.document != desired_document,
                existing.enabled != enabled,
            )
        )
        if changed:
            existing.policy_kind = policy_kind
            existing.service = service
            existing.service_type = service_type
            existing.name = name
            existing.document = desired_document
            existing.enabled = enabled
            existing.revision += 1
            self.session.flush()
        return existing, False, changed

    def disable(self, policy_id: uuid.UUID | str) -> GovernancePolicy:
        policy = self.get(policy_id)
        if not policy.enabled and policy.document.get("isEnabled") is False:
            return policy
        document = deepcopy(policy.document)
        document["isEnabled"] = False
        policy.document = document
        policy.enabled = False
        policy.revision += 1
        self.session.flush()
        return policy
