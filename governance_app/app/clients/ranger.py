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


def canonical_hash(document: dict) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def normalize_policy(document: dict | None) -> dict | None:
    if not document:
        return None
    keep = {
        "service",
        "name",
        "description",
        "isEnabled",
        "resources",
        "policyItems",
        "denyPolicyItems",
        "allowExceptions",
        "denyExceptions",
    }
    normalized = {key: deepcopy(value) for key, value in document.items() if key in keep}
    description = normalized.get("description")
    if isinstance(description, str) and " | managed-by=dg-backend;" in description:
        normalized["description"] = description.split(" | managed-by=dg-backend;", 1)[0]
    for item_key in (
        "policyItems",
        "denyPolicyItems",
        "allowExceptions",
        "denyExceptions",
    ):
        if item_key in normalized:
            normalized[item_key] = sorted(
                normalized[item_key],
                key=lambda item: json.dumps(item, sort_keys=True),
            )
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
        document = desired.ranger_document()
        desired_hash = canonical_hash(normalize_policy(document) or {})
        marker = (
            f"managed-by=dg-backend;policy-key={desired.policy_key};"
            f"desired-sha256={desired_hash}"
        )
        document["description"] = f"{desired.description} | {marker}"[:4000]
        existing = self.find_by_name(desired.name)
        observed_hash = canonical_hash(normalize_policy(existing) or {}) if existing else None

        if self.dry_run:
            return {
                "action": ReconciliationAction.DRY_RUN.value,
                "desired_hash": desired_hash,
                "observed_hash": observed_hash,
                "policy_id": str(existing.get("id")) if existing else None,
                "document": document,
            }

        if not existing:
            created = self._request("POST", "/policy/apply", json=document)
            return {
                "action": ReconciliationAction.CREATE.value,
                "desired_hash": desired_hash,
                "observed_hash": None,
                "policy_id": str(created.get("id")),
                "document": created,
            }

        existing_owned = "managed-by=dg-backend" in str(existing.get("description", ""))
        if not existing_owned:
            raise ExternalSystemError(
                f"Ranger policy {desired.name!r} exists but is not owned by this backend",
                system="ranger",
                retryable=False,
            )
        if normalize_policy(existing) == normalize_policy(document):
            return {
                "action": ReconciliationAction.NO_CHANGE.value,
                "desired_hash": desired_hash,
                "observed_hash": observed_hash,
                "policy_id": str(existing.get("id")),
                "document": existing,
            }

        policy_id = existing.get("id")
        updated = self._request(
            "PUT",
            f"/policy/{policy_id}",
            json={**document, "id": policy_id},
        )
        return {
            "action": ReconciliationAction.UPDATE.value,
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
