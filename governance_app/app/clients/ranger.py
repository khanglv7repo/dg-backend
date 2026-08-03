from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import ExternalSystemError
from app.models.enums import ReconciliationAction
from app.schemas.policy import DesiredPolicy

SERVER_MANAGED_POLICY_FIELDS = {
    "id",
    "guid",
    "version",
    "createTime",
    "updateTime",
    "createdBy",
    "updatedBy",
}


def canonical_hash(document: dict) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sorted_json_list(values: list[Any]) -> list[Any]:
    return sorted(values, key=lambda item: json.dumps(item, sort_keys=True))


def _normalize_policy_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(item)
    for key in ("users", "groups"):
        if isinstance(normalized.get(key), list):
            values = sorted(str(value) for value in normalized[key])
            if values:
                normalized[key] = values
            else:
                normalized.pop(key, None)
    if isinstance(normalized.get("accesses"), list):
        normalized["accesses"] = _sorted_json_list(normalized["accesses"])
    if isinstance(normalized.get("conditions"), list):
        conditions = _sorted_json_list(normalized["conditions"])
        if conditions:
            normalized["conditions"] = conditions
        else:
            normalized.pop("conditions", None)
    normalized.setdefault("delegateAdmin", False)
    return normalized


def normalize_policy(document: dict | None) -> dict | None:
    """Remove Ranger server state and canonicalize unordered policy collections."""

    if not document:
        return None
    normalized = deepcopy(document)
    for key in SERVER_MANAGED_POLICY_FIELDS:
        normalized.pop(key, None)

    description = normalized.get("description")
    if isinstance(description, str) and " | managed-by=dg-backend;" in description:
        normalized["description"] = description.split(
            " | managed-by=dg-backend;",
            1,
        )[0]

    resources = normalized.get("resources")
    if isinstance(resources, dict):
        for resource in resources.values():
            if isinstance(resource, dict) and isinstance(resource.get("values"), list):
                resource["values"] = sorted(str(value) for value in resource["values"])

    for item_key in (
        "policyItems",
        "denyPolicyItems",
        "allowExceptions",
        "denyExceptions",
        "dataMaskPolicyItems",
        "rowFilterPolicyItems",
    ):
        items = normalized.get(item_key)
        if isinstance(items, list):
            normalized_items = _sorted_json_list(
                [
                    _normalize_policy_item(item)
                    if isinstance(item, dict)
                    else item
                    for item in items
                ]
            )
            if normalized_items:
                normalized[item_key] = normalized_items
            else:
                normalized.pop(item_key, None)
    return normalized


class RangerClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str | None,
        password: str | None,
        service_name: str,
        dry_run: bool = True,
        timeout: float = 15.0,
    ) -> None:
        auth = (username, password or "") if username else None
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        self.service_name = service_name
        self.dry_run = dry_run

    def close(self) -> None:
        self.client.close()

    def health(self) -> dict:
        return self._request(
            "GET",
            f"/service/name/{quote(self.service_name, safe='')}",
        )

    def list_policies(self) -> list[dict]:
        response = self._request(
            "GET",
            f"/service/{quote(self.service_name, safe='')}/policy",
        )
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if isinstance(response, dict):
            for key in ("policies", "list", "data"):
                values = response.get(key)
                if isinstance(values, list):
                    return [item for item in values if isinstance(item, dict)]
        return []

    def find_by_name(self, name: str) -> dict | None:
        path = (
            f"/service/{quote(self.service_name, safe='')}/policy/"
            f"{quote(name, safe='')}"
        )
        try:
            return self._request("GET", path)
        except ExternalSystemError as exc:
            if exc.status_code == 404:
                return None
            raise

    def reconcile(self, desired: DesiredPolicy) -> dict[str, Any]:
        """Compatibility adapter for the legacy custom DesiredPolicy model."""

        return self.reconcile_document(
            policy_key=desired.policy_key,
            document=desired.ranger_document(),
        )

    def reconcile_document(
        self,
        *,
        policy_key: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """Reconcile one native RangerPolicy JSON document."""

        clean = normalize_policy(document) or {}
        service = str(clean.get("service") or "")
        name = str(clean.get("name") or "")
        if service != self.service_name:
            raise ExternalSystemError(
                f"policy {name!r} targets service {service!r}, but this client owns "
                f"{self.service_name!r}",
                system="ranger",
                retryable=False,
            )
        if not name:
            raise ExternalSystemError(
                "Ranger policy document is missing name",
                system="ranger",
                retryable=False,
            )

        desired_hash = canonical_hash(clean)
        base_description = str(clean.get("description") or "")
        marker = (
            f"managed-by=dg-backend;policy-key={policy_key};"
            f"desired-sha256={desired_hash}"
        )
        outbound = deepcopy(clean)
        outbound["description"] = f"{base_description} | {marker}"[:4000]

        existing = self.find_by_name(name)
        observed_hash = canonical_hash(normalize_policy(existing) or {}) if existing else None

        if existing is not None:
            existing_owned = "managed-by=dg-backend" in str(
                existing.get("description", "")
            )
            if not existing_owned:
                raise ExternalSystemError(
                    f"Ranger policy {name!r} exists but is not owned by this backend",
                    system="ranger",
                    retryable=False,
                )

        if normalize_policy(existing) == clean:
            return {
                "action": ReconciliationAction.NO_CHANGE.value,
                "desired_hash": desired_hash,
                "observed_hash": observed_hash,
                "policy_id": str(existing.get("id")) if existing else None,
                "document": existing or outbound,
            }

        desired_enabled = bool(clean.get("isEnabled", True))
        if existing is None and not desired_enabled:
            return {
                "action": ReconciliationAction.NO_CHANGE.value,
                "desired_hash": desired_hash,
                "observed_hash": None,
                "policy_id": None,
                "document": outbound,
            }

        if self.dry_run:
            return {
                "action": ReconciliationAction.DRY_RUN.value,
                "desired_hash": desired_hash,
                "observed_hash": observed_hash,
                "policy_id": str(existing.get("id")) if existing else None,
                "document": outbound,
            }

        if existing is None:
            created = self._request("POST", "/policy/apply", json=outbound)
            return {
                "action": ReconciliationAction.CREATE.value,
                "desired_hash": desired_hash,
                "observed_hash": None,
                "policy_id": str(created.get("id")),
                "document": created,
            }

        policy_id = existing.get("id")
        if policy_id is None:
            raise ExternalSystemError(
                f"Ranger policy {name!r} is missing id",
                system="ranger",
                retryable=False,
            )
        updated = self._request(
            "PUT",
            f"/policy/{policy_id}",
            json={**outbound, "id": policy_id},
        )
        action = (
            ReconciliationAction.DISABLE.value
            if not desired_enabled and bool(existing.get("isEnabled", True))
            else ReconciliationAction.UPDATE.value
        )
        return {
            "action": action,
            "desired_hash": desired_hash,
            "observed_hash": observed_hash,
            "policy_id": str(policy_id),
            "document": updated,
        }

    def disable_policy(self, policy: dict) -> dict[str, Any]:
        policy_id = str(policy.get("id"))
        doc = deepcopy(policy)
        doc["isEnabled"] = False
        desired_hash = canonical_hash(normalize_policy(doc) or {})
        observed_hash = canonical_hash(normalize_policy(policy) or {})
        if self.dry_run:
            return {
                "action": ReconciliationAction.DRY_RUN.value,
                "desired_hash": desired_hash,
                "observed_hash": observed_hash,
                "policy_id": policy_id,
                "document": doc,
            }
        updated = self._request("PUT", f"/policy/{policy_id}", json=doc)
        return {
            "action": ReconciliationAction.DISABLE.value,
            "desired_hash": desired_hash,
            "observed_hash": observed_hash,
            "policy_id": policy_id,
            "document": updated,
        }

    def delete_policy(self, policy: dict) -> dict[str, Any]:
        policy_id = str(policy.get("id"))
        observed_hash = canonical_hash(normalize_policy(policy) or {})
        if self.dry_run:
            return {
                "action": ReconciliationAction.DRY_RUN.value,
                "desired_hash": "",
                "observed_hash": observed_hash,
                "policy_id": policy_id,
                "document": policy,
            }
        self._request("DELETE", f"/policy/{policy_id}")
        return {
            "action": ReconciliationAction.DELETE.value,
            "desired_hash": "",
            "observed_hash": observed_hash,
            "policy_id": policy_id,
            "document": {},
        }

    def reconcile_removal(
        self,
        policy_name: str,
        allow_delete: bool = False,
    ) -> dict[str, Any] | None:
        existing = self.find_by_name(policy_name)
        if not existing:
            return None
        existing_owned = "managed-by=dg-backend" in str(existing.get("description", ""))
        if not existing_owned:
            raise ExternalSystemError(
                f"Ranger policy {policy_name!r} exists but is not owned by this backend",
                system="ranger",
                retryable=False,
            )
        if allow_delete:
            return self.delete_policy(existing)
        return self.disable_policy(existing)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ExternalSystemError(
                "Ranger request timed out",
                system="ranger",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalSystemError(
                "Ranger connection failed",
                system="ranger",
                retryable=True,
            ) from exc
        if response.is_error:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise ExternalSystemError(
                f"Ranger returned HTTP {response.status_code}: {response.text[:500]}",
                system="ranger",
                retryable=retryable,
                status_code=response.status_code,
            )
        return response.json() if response.content else {}
