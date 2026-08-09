from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.clients.ranger import canonical_hash, normalize_policy
from app.schemas.data_access_policy import (
    AccessDecision,
    LogicalDataAccessPolicy,
    LogicalMaskIntent,
)

TRINO_SERVICE_TYPE = "trino"
RANGER_POLICY_TYPE_ACCESS = 0
RANGER_POLICY_TYPE_MASK = 1
RANGER_POLICY_TYPE_ROW_FILTER = 2
RANGER_MASK_TYPES = {
    # Apache Ranger 2.8.0 Trino service definition:
    # logical MASK -> native MASK (Redact).
    LogicalMaskIntent.MASK: "MASK",
}


@dataclass(frozen=True, slots=True)
class CompiledProjection:
    projection_type: str
    projection_key: str
    ranger_service: str
    ranger_policy_name: str
    document: dict[str, Any]
    desired_checksum: str


class PolicyCompiler:
    """Deterministically compile one logical policy into Ranger projections."""

    def __init__(self, *, ranger_service_name: str) -> None:
        self.ranger_service_name = ranger_service_name

    def compile(
        self,
        *,
        policy_key: str,
        version: int,
        logical_policy: LogicalDataAccessPolicy,
    ) -> list[CompiledProjection]:
        projections: list[CompiledProjection] = []

        if logical_policy.access:
            document = self._compile_access(policy_key, logical_policy)
            projections.append(
                self._projection(
                    projection_type="ACCESS",
                    projection_key="access",
                    policy_key=policy_key,
                    document=document,
                )
            )

        for column, intent in sorted(logical_policy.masks.items()):
            projection_key = f"mask:{hashlib.sha256(column.encode()).hexdigest()[:12]}"
            document = self._compile_mask(
                policy_key=policy_key,
                logical_policy=logical_policy,
                column=column,
                intent=intent,
            )
            projections.append(
                self._projection(
                    projection_type="MASK",
                    projection_key=projection_key,
                    policy_key=policy_key,
                    document=document,
                )
            )

        if logical_policy.row_filter is not None:
            document = self._compile_row_filter(policy_key, logical_policy)
            projections.append(
                self._projection(
                    projection_type="ROW_FILTER",
                    projection_key="row-filter",
                    policy_key=policy_key,
                    document=document,
                )
            )

        # ``version`` intentionally does not enter the policy name/checksum.
        # Version traceability is carried by the projection row and Ranger
        # ownership marker; stable names let a newer immutable version update
        # the same runtime projection instead of leaving duplicate policies.
        _ = version
        return projections

    def _projection(
        self,
        *,
        projection_type: str,
        projection_key: str,
        policy_key: str,
        document: dict[str, Any],
    ) -> CompiledProjection:
        desired = normalize_policy(document) or {}
        return CompiledProjection(
            projection_type=projection_type,
            projection_key=projection_key,
            ranger_service=self.ranger_service_name,
            ranger_policy_name=str(document["name"]),
            document=document,
            desired_checksum=canonical_hash(desired),
        )

    def _compile_access(
        self,
        policy_key: str,
        logical_policy: LogicalDataAccessPolicy,
    ) -> dict[str, Any]:
        allow_ops = sorted(
            operation
            for operation, decision in logical_policy.access.items()
            if decision == AccessDecision.ALLOW
        )
        deny_ops = sorted(
            operation
            for operation, decision in logical_policy.access.items()
            if decision == AccessDecision.DENY
        )
        subjects = self._subjects(logical_policy)

        document = self._base_document(
            policy_key=policy_key,
            name=self._policy_name(policy_key, "access"),
            policy_type=RANGER_POLICY_TYPE_ACCESS,
            resources={
                **self._table_resources(logical_policy),
                "column": self._resource("*"),
            },
            description=f"Data-access projection for {policy_key} [ACCESS]",
        )
        if allow_ops:
            document["policyItems"] = [
                {
                    **subjects,
                    "accesses": [
                        {"type": operation, "isAllowed": True}
                        for operation in allow_ops
                    ],
                    "delegateAdmin": False,
                }
            ]
        if deny_ops:
            document["denyPolicyItems"] = [
                {
                    **subjects,
                    "accesses": [
                        {"type": operation, "isAllowed": True}
                        for operation in deny_ops
                    ],
                    "delegateAdmin": False,
                }
            ]
        return document

    def _compile_mask(
        self,
        *,
        policy_key: str,
        logical_policy: LogicalDataAccessPolicy,
        column: str,
        intent: LogicalMaskIntent,
    ) -> dict[str, Any]:
        native_mask_type = RANGER_MASK_TYPES[intent]
        column_hash = hashlib.sha256(column.encode()).hexdigest()[:10]
        subjects = self._subjects(logical_policy)
        document = self._base_document(
            policy_key=policy_key,
            name=self._policy_name(
                policy_key,
                f"mask-{self._slug(column, 24)}-{column_hash}",
            ),
            policy_type=RANGER_POLICY_TYPE_MASK,
            resources={
                **self._table_resources(logical_policy),
                "column": self._resource(column),
            },
            description=f"Data-access projection for {policy_key} [MASK:{column}]",
        )
        document["dataMaskPolicyItems"] = [
            {
                **subjects,
                "accesses": [{"type": "select", "isAllowed": True}],
                "dataMaskInfo": {"dataMaskType": native_mask_type},
                "delegateAdmin": False,
            }
        ]
        return document

    def _compile_row_filter(
        self,
        policy_key: str,
        logical_policy: LogicalDataAccessPolicy,
    ) -> dict[str, Any]:
        subjects = self._subjects(logical_policy)
        document = self._base_document(
            policy_key=policy_key,
            name=self._policy_name(policy_key, "row-filter"),
            policy_type=RANGER_POLICY_TYPE_ROW_FILTER,
            resources=self._table_resources(logical_policy),
            description=f"Data-access projection for {policy_key} [ROW_FILTER]",
        )
        document["rowFilterPolicyItems"] = [
            {
                **subjects,
                "accesses": [{"type": "select", "isAllowed": True}],
                "rowFilterInfo": {"filterExpr": logical_policy.row_filter},
                "delegateAdmin": False,
            }
        ]
        return document

    def _base_document(
        self,
        *,
        policy_key: str,
        name: str,
        policy_type: int,
        resources: dict[str, Any],
        description: str,
    ) -> dict[str, Any]:
        _ = policy_key
        return {
            "isEnabled": True,
            "service": self.ranger_service_name,
            "name": name,
            "policyType": policy_type,
            "policyPriority": 0,
            "description": description,
            "isAuditEnabled": True,
            "resources": resources,
            "serviceType": TRINO_SERVICE_TYPE,
            "isDenyAllElse": False,
        }

    @staticmethod
    def _resource(value: str) -> dict[str, Any]:
        return {
            "values": [value],
            "isExcludes": False,
            "isRecursive": False,
        }

    def _table_resources(
        self,
        logical_policy: LogicalDataAccessPolicy,
    ) -> dict[str, Any]:
        resource = logical_policy.resource
        return {
            "catalog": self._resource(resource.catalog),
            "schema": self._resource(resource.schema_name),
            "table": self._resource(resource.table),
        }

    @staticmethod
    def _subjects(logical_policy: LogicalDataAccessPolicy) -> dict[str, list[str]]:
        users = sorted(
            {subject.name for subject in logical_policy.subjects if subject.type.value == "USER"}
        )
        groups = sorted(
            {subject.name for subject in logical_policy.subjects if subject.type.value == "GROUP"}
        )
        result: dict[str, list[str]] = {}
        if users:
            result["users"] = users
        if groups:
            result["groups"] = groups
        return result

    def _policy_name(self, policy_key: str, suffix: str) -> str:
        digest = hashlib.sha256(policy_key.encode()).hexdigest()[:12]
        slug = self._slug(policy_key, 42)
        return f"dg-r4-{slug}-{digest}-{suffix}"[:255]

    @staticmethod
    def _slug(value: str, limit: int) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-_.").lower()
        return (normalized or "policy")[:limit]
