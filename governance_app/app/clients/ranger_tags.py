from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import ExternalSystemError


class RangerTagStoreClient:
    """Narrow client for Ranger Admin's /service/tags TagREST API.

    This is the Flow-B boundary. It synchronizes the current Confirmed
    OpenMetadata tag state into Ranger's tag store; it never creates access
    policies. Access policies are independently reconciled at backend startup.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str | None,
        password: str | None,
        resource_service_name: str,
        dry_run: bool = True,
        timeout: float = 15.0,
    ) -> None:
        auth = (username, password or "") if username else None
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self.resource_service_name = resource_service_name
        self.dry_run = dry_run

    def close(self) -> None:
        self.client.close()

    def health(self) -> list[dict]:
        return self.list_resources()

    def list_tag_definitions(self) -> list[dict]:
        return self._as_list(self._request("GET", "/tagdefs"))

    def list_tags(self) -> list[dict]:
        return self._as_list(self._request("GET", "/tags"))

    def list_resources(self) -> list[dict]:
        service = quote(self.resource_service_name, safe="")
        return self._as_list(
            self._request("GET", f"/resources/service/{service}")
        )

    def list_tag_resource_maps(self) -> list[dict]:
        return self._as_list(self._request("GET", "/tagresourcemaps"))

    def ensure_tag_definition(self, tag_type: str) -> dict:
        existing = next(
            (
                item
                for item in self.list_tag_definitions()
                if str(item.get("name") or "") == tag_type
            ),
            None,
        )
        if existing is not None:
            return existing

        if self.dry_run:
            return {
                "name": tag_type,
                "guid": f"dry-run-tagdef:{tag_type}",
                "source": "dg-backend",
                "isEnabled": True,
            }

        return self._request(
            "POST",
            "/tagdefs",
            params={"updateIfExists": "true"},
            json={
                "name": tag_type,
                "source": "dg-backend",
                "attributeDefs": [],
                "isEnabled": True,
            },
        )

    def ensure_tag(self, tag_type: str) -> dict:
        path = f"/tags/type/{quote(tag_type, safe='')}"
        values = self._as_list(self._request("GET", path, allow_404=True))
        existing = next(
            (
                item
                for item in values
                if str(item.get("type") or "") == tag_type
            ),
            None,
        )
        if existing is not None:
            return existing

        self.ensure_tag_definition(tag_type)

        if self.dry_run:
            return {
                "type": tag_type,
                "guid": f"dry-run-tag:{tag_type}",
                "options": {"managedBy": "dg-backend"},
                "isEnabled": True,
            }

        return self._request(
            "POST",
            "/tags",
            params={"updateIfExists": "true"},
            json={
                "type": tag_type,
                "attributes": {},
                "options": {
                    "managedBy": "dg-backend",
                    "source": "openmetadata",
                },
                "isEnabled": True,
            },
        )

    def ensure_resource(
        self,
        *,
        entity_fqn: str,
        field_path: str | None,
    ) -> dict:
        identity = self._identity(field_path)
        existing = self._find_managed_resource(
            entity_fqn=entity_fqn,
            identity=identity,
        )
        desired = self._resource_document(
            entity_fqn=entity_fqn,
            field_path=field_path,
        )

        if existing is not None:
            if self._normalized_resource_elements(
                existing.get("resourceElements")
            ) == self._normalized_resource_elements(desired["resourceElements"]):
                return existing

            if self.dry_run:
                return {**existing, **desired}

            resource_id = existing.get("id")
            if resource_id is None:
                raise ExternalSystemError(
                    "Ranger managed resource is missing id",
                    system="ranger-tag-store",
                    retryable=False,
                )
            return self._request(
                "PUT",
                f"/resource/{resource_id}",
                json={**existing, **desired},
            )

        if self.dry_run:
            return {
                **desired,
                "guid": f"dry-run-resource:{entity_fqn}:{identity}",
            }

        return self._request(
            "POST",
            "/resources",
            params={"updateIfExists": "true"},
            json=desired,
        )

    def ensure_tag_resource_map(
        self,
        *,
        resource_guid: str,
        tag_guid: str,
    ) -> dict:
        existing = self._request(
            "GET",
            "/tagresourcemap/tag-resource-guid",
            params={
                "resourceGuid": resource_guid,
                "tagGuid": tag_guid,
            },
            allow_404=True,
        )
        if isinstance(existing, dict) and existing.get("id") is not None:
            return existing

        if self.dry_run:
            return {
                "guid": f"dry-run-map:{resource_guid}:{tag_guid}",
                "resourceGuid": resource_guid,
                "tagGuid": tag_guid,
            }

        return self._request(
            "POST",
            "/tagresourcemaps",
            params={
                "resource-guid": resource_guid,
                "tag-guid": tag_guid,
                "lenient": "true",
            },
        )

    def reconcile_assignments(
        self,
        *,
        entity_fqn: str,
        entity_tags: list[str],
        field_tags: dict[str, list[str]],
    ) -> dict[str, Any]:
        expected = self._expected_assignments(
            entity_tags=entity_tags,
            field_tags=field_tags,
        )

        if self.dry_run:
            return {
                "action": "DRY_RUN",
                "entity_fqn": entity_fqn,
                "expected_assignments": [
                    {"field_path": field_path, "tag": tag}
                    for field_path, tag in sorted(expected)
                ],
                "created_or_existing": [],
                "removed": [],
            }

        created_or_existing: list[dict[str, str]] = []
        for field_path, tag_type in sorted(expected):
            tag = self.ensure_tag(tag_type)
            resource = self.ensure_resource(
                entity_fqn=entity_fqn,
                field_path=None if field_path == "$entity" else field_path,
            )
            tag_guid = str(tag.get("guid") or "")
            resource_guid = str(resource.get("guid") or "")
            if not tag_guid or not resource_guid:
                raise ExternalSystemError(
                    "Ranger tag/resource response did not contain guid",
                    system="ranger-tag-store",
                    retryable=False,
                )
            mapping = self.ensure_tag_resource_map(
                resource_guid=resource_guid,
                tag_guid=tag_guid,
            )
            created_or_existing.append(
                {
                    "field_path": field_path,
                    "tag": tag_type,
                    "resource_guid": resource_guid,
                    "tag_guid": tag_guid,
                    "map_id": str(mapping.get("id") or ""),
                }
            )

        removed = self._remove_stale_assignments(
            entity_fqn=entity_fqn,
            expected=expected,
        )

        return {
            "action": "SYNC",
            "entity_fqn": entity_fqn,
            "expected_assignments": [
                {"field_path": field_path, "tag": tag}
                for field_path, tag in sorted(expected)
            ],
            "created_or_existing": created_or_existing,
            "removed": removed,
        }

    def _remove_stale_assignments(
        self,
        *,
        entity_fqn: str,
        expected: set[tuple[str, str]],
    ) -> list[dict[str, str]]:
        resources = {
            int(item["id"]): item
            for item in self.list_resources()
            if item.get("id") is not None
            and self._is_managed_entity_resource(item, entity_fqn)
        }
        if not resources:
            return []

        tags = {
            int(item["id"]): item
            for item in self.list_tags()
            if item.get("id") is not None
        }

        removed: list[dict[str, str]] = []
        for mapping in self.list_tag_resource_maps():
            resource_id = mapping.get("resourceId")
            tag_id = mapping.get("tagId")
            if resource_id is None or tag_id is None:
                continue

            resource = resources.get(int(resource_id))
            tag = tags.get(int(tag_id))
            if resource is None or tag is None:
                continue

            field_path = str(
                (resource.get("additionalInfo") or {}).get("fieldPath")
                or "$entity"
            )
            tag_type = str(tag.get("type") or "")
            if not tag_type or (field_path, tag_type) in expected:
                continue

            mapping_id = mapping.get("id")
            if mapping_id is None:
                continue

            self._request("DELETE", f"/tagresourcemap/{mapping_id}")
            removed.append(
                {
                    "field_path": field_path,
                    "tag": tag_type,
                    "map_id": str(mapping_id),
                }
            )

        return removed

    def _find_managed_resource(
        self,
        *,
        entity_fqn: str,
        identity: str,
    ) -> dict | None:
        for item in self.list_resources():
            info = item.get("additionalInfo") or {}
            if str(info.get("managedBy") or "") != "dg-backend":
                continue
            if str(info.get("openmetadataFqn") or "") != entity_fqn:
                continue
            if str(info.get("fieldPath") or "$entity") != identity:
                continue
            return item
        return None

    def _resource_document(
        self,
        *,
        entity_fqn: str,
        field_path: str | None,
    ) -> dict[str, Any]:
        elements: dict[str, Any] = {
            "table": {
                "values": [entity_fqn],
                "isRecursive": False,
                "isExcludes": False,
            }
        }

        if field_path is not None:
            field_name = (
                field_path.split(".", 1)[1]
                if field_path.startswith("columns.")
                else field_path
            )
            elements["column"] = {
                "values": [field_name],
                "isRecursive": False,
                "isExcludes": False,
            }

        return {
            "serviceName": self.resource_service_name,
            "resourceElements": elements,
            "additionalInfo": {
                "managedBy": "dg-backend",
                "source": "openmetadata",
                "openmetadataFqn": entity_fqn,
                "fieldPath": self._identity(field_path),
            },
            "isEnabled": True,
        }

    @staticmethod
    def _identity(field_path: str | None) -> str:
        return field_path or "$entity"

    @staticmethod
    def _expected_assignments(
        *,
        entity_tags: list[str],
        field_tags: dict[str, list[str]],
    ) -> set[tuple[str, str]]:
        expected = {
            ("$entity", str(tag))
            for tag in entity_tags
            if str(tag).strip()
        }
        for field_path, tags in field_tags.items():
            for tag in tags:
                if str(tag).strip():
                    expected.add((str(field_path), str(tag)))
        return expected

    @staticmethod
    def _is_managed_entity_resource(resource: dict, entity_fqn: str) -> bool:
        info = resource.get("additionalInfo") or {}
        return (
            str(info.get("managedBy") or "") == "dg-backend"
            and str(info.get("openmetadataFqn") or "") == entity_fqn
        )

    @staticmethod
    def _normalized_resource_elements(value: Any) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, tuple[str, ...]] = {}
        for key, item in value.items():
            if not isinstance(item, dict):
                continue
            normalized[str(key)] = tuple(
                sorted(str(entry) for entry in (item.get("values") or []))
            )
        return normalized

    @staticmethod
    def _as_list(value: Any) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for key in ("list", "data", "tags", "resources"):
                items = value.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        return []

    def _request(
        self,
        method: str,
        path: str,
        *,
        allow_404: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ExternalSystemError(
                "Ranger tag-store request timed out",
                system="ranger-tag-store",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalSystemError(
                "Ranger tag-store connection failed",
                system="ranger-tag-store",
                retryable=True,
            ) from exc

        if allow_404 and response.status_code == 404:
            return None

        if response.is_error:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise ExternalSystemError(
                f"Ranger tag store returned HTTP {response.status_code}: "
                f"{response.text[:500]}",
                system="ranger-tag-store",
                retryable=retryable,
                status_code=response.status_code,
            )

        return response.json() if response.content else {}
