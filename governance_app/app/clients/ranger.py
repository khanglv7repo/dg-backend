from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.errors import ExternalSystemError
from app.models.enums import ReconciliationAction

SERVER_MANAGED_POLICY_FIELDS = {
    "id",
    "guid",
    "version",
    "createTime",
    "updateTime",
    "createdBy",
    "updatedBy",
    "resourceSignature",
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
                resource["values"] = sorted(
                    str(value) for value in resource["values"]
                )

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


def _marker_value(value: object, *, field: str) -> str:
    text = str(value)
    if any(char in text for char in (";", "\n", "\r")):
        raise ExternalSystemError(
            f"invalid Ranger ownership marker {field}",
            system="ranger",
            retryable=False,
        )
    return text


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
        clean_base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=clean_base_url,
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        parsed = urlsplit(clean_base_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
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

    def find_user(self, name: str) -> dict | None:
        """Read one Ranger user through the Ranger 2.8 XUserREST contract."""

        url = (
            f"{self.origin}/service/xusers/users/userName/"
            f"{quote(name, safe='')}"
        )
        try:
            result = self._request("GET", url)
        except ExternalSystemError as exc:
            if exc.status_code == 404:
                return None
            raise
        return result if isinstance(result, dict) else None

    def find_group(self, name: str) -> dict | None:
        """Read one Ranger group through the Ranger 2.8 XUserREST contract."""

        url = (
            f"{self.origin}/service/xusers/groups/groupName/"
            f"{quote(name, safe='')}"
        )
        try:
            result = self._request("GET", url)
        except ExternalSystemError as exc:
            if exc.status_code == 404:
                return None
            raise
        return result if isinstance(result, dict) else None

    def user_exists(self, name: str) -> bool:
        result = self.find_user(name)
        return bool(result and str(result.get("name") or "") == name)

    def group_exists(self, name: str) -> bool:
        result = self.find_group(name)
        return bool(result and str(result.get("name") or "") == name)

    @staticmethod
    def owns_policy(document: dict | None, *, policy_key: str) -> bool:
        if not document:
            return False
        safe_key = _marker_value(policy_key, field="policy-key")
        marker = f"managed-by=dg-backend;policy-key={safe_key};"
        return marker in str(document.get("description") or "")

    def reconcile_document(
        self,
        *,
        policy_key: str,
        document: dict[str, Any],
        ownership: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Reconcile one native RangerPolicy JSON document.

        ``policy_key`` is the ownership boundary. Existing documents are writable
        only when their description proves the same backend policy key. Optional
        ownership metadata is operational traceability (for example immutable
        policy version and projection kind), not domain truth.
        """

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

        safe_policy_key = _marker_value(policy_key, field="policy-key")
        desired_hash = canonical_hash(clean)
        marker_parts = [
            "managed-by=dg-backend",
            f"policy-key={safe_policy_key}",
        ]
        for key, value in sorted((ownership or {}).items()):
            safe_key = _marker_value(key, field="metadata-key")
            safe_value = _marker_value(value, field=key)
            marker_parts.append(f"{safe_key}={safe_value}")
        marker_parts.append(f"desired-sha256={desired_hash}")
        marker = ";".join(marker_parts) + ";"

        base_description = str(clean.get("description") or "")
        suffix = f" | {marker}"
        max_description_length = 4000
        max_base_length = max(0, max_description_length - len(suffix))

        outbound = deepcopy(clean)
        outbound["description"] = f"{base_description[:max_base_length]}{suffix}"

        existing = self.find_by_name(name)
        observed_hash = (
            canonical_hash(normalize_policy(existing) or {})
            if existing
            else None
        )

        if existing is not None and not self.owns_policy(
            existing,
            policy_key=safe_policy_key,
        ):
            raise ExternalSystemError(
                f"Ranger policy {name!r} exists but is not owned by policy "
                f"{safe_policy_key!r}",
                system="ranger",
                retryable=False,
            )

        existing_description = str((existing or {}).get("description") or "")
        marker_current = all(
            token in existing_description
            for token in [
                f"managed-by=dg-backend;policy-key={safe_policy_key};",
                f"desired-sha256={desired_hash};",
                *[
                    f"{_marker_value(key, field='metadata-key')}="
                    f"{_marker_value(value, field=key)};"
                    for key, value in sorted((ownership or {}).items())
                ],
            ]
        )

        if normalize_policy(existing) == clean and marker_current:
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
            if created and isinstance(created, dict) and created.get("name") != name and created.get("id"):
                policy_id = created["id"]
                created = self._request(
                    "PUT",
                    f"/policy/{policy_id}",
                    json={**outbound, "id": policy_id},
                )
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
