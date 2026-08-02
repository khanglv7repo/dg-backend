from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import ExternalSystemError, NotFoundError


class OpenMetadataClient:
    """Narrow OpenMetadata 1.13 adapter.

    OpenMetadata remains the metadata and review system of record. Ranger
    reconciliation always reads the current Confirmed tag state from this
    adapter instead of trusting a possibly partial ChangeEvent payload.
    """

    def __init__(self, *, base_url: str, token: str | None, timeout: float = 15.0) -> None:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    def collection_for(entity_type: str) -> str:
        mapping = {
            "table": "tables",
            "databaseSchema": "databaseSchemas",
            "topic": "topics",
        }
        return mapping.get(
            entity_type,
            entity_type if entity_type.endswith("s") else f"{entity_type}s",
        )

    @staticmethod
    def build_entity_link(*, entity_type: str, entity_fqn: str, field_path: str | None) -> str:
        if not field_path:
            return f"<#E::{entity_type}::{entity_fqn}>"
        if field_path.startswith("columns."):
            column_name = field_path.split(".", 1)[1]
            return f"<#E::{entity_type}::{entity_fqn}::columns::{column_name}>"
        return f"<#E::{entity_type}::{entity_fqn}::{field_path}>"

    @staticmethod
    def tag_label(
        tag_fqn: str,
        *,
        label_type: str,
        state: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "tagFQN": tag_fqn,
            "source": "Classification",
            "labelType": label_type,
            "state": state,
        }
        if reason:
            value["reason"] = reason[:1000]
        return value

    def close(self) -> None:
        self.client.close()

    def health(self) -> dict:
        return self._request("GET", "/v1/system/version")

    def get_entity(self, *, entity_type: str, fqn: str, fields: str = "tags,columns") -> dict:
        collection = self.collection_for(entity_type)
        encoded = quote(fqn, safe="")
        response = self._request(
            "GET",
            f"/v1/{collection}/name/{encoded}",
            params={"fields": fields},
        )
        if not response:
            raise NotFoundError(f"OpenMetadata entity {entity_type}:{fqn} was not found")
        return response

    def get_column(
        self,
        *,
        column_fqn: str,
        entity_type: str = "table",
        fields: str = "tags",
    ) -> dict:
        encoded = quote(column_fqn, safe="")
        return self._request(
            "GET",
            f"/v1/columns/name/{encoded}",
            params={"entityType": entity_type, "fields": fields},
        )

    def get_confirmed_tag_snapshot(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
    ) -> dict[str, Any]:
        """Read the live Confirmed tag state used to drive Ranger.

        ChangeEvent payloads are useful as triggers, but they are not treated as
        the enforcement source of truth. This method reads the current entity
        from OpenMetadata and returns both the tag-centric and field-centric
        views required by the policy resolver.
        """

        entity = self.get_entity(
            entity_type=entity_type,
            fqn=entity_fqn,
            fields="tags,columns",
        )

        entity_tags = self._confirmed_tag_fqns(entity.get("tags", []))
        field_tags: dict[str, list[str]] = {}
        all_field_paths: list[str] = []

        for column in entity.get("columns", []) or []:
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue

            field_path = f"columns.{column_name}"
            all_field_paths.append(field_path)

            confirmed = self._confirmed_tag_fqns(column.get("tags", []))
            if confirmed:
                field_tags[field_path] = confirmed

        field_paths: dict[str, list[str]] = {}
        for field_path, tags in field_tags.items():
            for tag in tags:
                field_paths.setdefault(tag, []).append(field_path)

        for tag, paths in field_paths.items():
            field_paths[tag] = sorted(set(paths))

        all_tags = set(entity_tags)
        for values in field_tags.values():
            all_tags.update(values)

        return {
            "entity_tags": entity_tags,
            "field_tags": {
                key: sorted(set(values))
                for key, values in sorted(field_tags.items())
            },
            "tags": sorted(all_tags),
            "field_paths": {
                key: sorted(set(values))
                for key, values in sorted(field_paths.items())
            },
            "all_field_paths": sorted(set(all_field_paths)),
        }

    def find_open_tag_suggestion(
        self,
        *,
        entity_fqn: str,
        marker: str,
        limit: int = 100,
    ) -> dict | None:
        response = self._request(
            "GET",
            "/v1/suggestions",
            params={"entityFQN": entity_fqn, "status": "Open", "limit": limit},
        )
        for item in response.get("data", []) or []:
            if item.get("type") == "SuggestTagLabel" and marker in str(
                item.get("description", "")
            ):
                return item
        return None

    def create_tag_suggestion(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        field_path: str | None,
        tags: list[str],
        description: str,
        label_type: str,
    ) -> dict:
        if not tags:
            raise ValueError("at least one tag is required for a suggestion")
        payload = {
            "description": description[:4000],
            "type": "SuggestTagLabel",
            "entityLink": self.build_entity_link(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                field_path=field_path,
            ),
            "tagLabels": [
                self.tag_label(tag, label_type=label_type, state="Suggested")
                for tag in sorted(set(tags))
            ],
        }
        return self._request("POST", "/v1/suggestions", json=payload)

    def apply_confirmed_tags(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        entity_tags: list[str],
        field_tags: dict[str, list[str]],
        label_type: str = "Automated",
    ) -> dict:
        if entity_tags:
            self._merge_entity_tags(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                tags=entity_tags,
                label_type=label_type,
            )

        observed_columns: dict[str, dict] = {}
        for field_path, tags in sorted(field_tags.items()):
            column_name = (
                field_path.split(".", 1)[1]
                if field_path.startswith("columns.")
                else field_path
            )
            column_fqn = f"{entity_fqn}.{column_name}"
            observed_columns[column_name] = self._merge_column_tags(
                column_fqn=column_fqn,
                entity_type=entity_type,
                tags=tags,
                label_type=label_type,
            )

        entity = self.get_entity(
            entity_type=entity_type,
            fqn=entity_fqn,
            fields="tags",
        )
        return {"entity": entity, "columns": observed_columns}

    def assert_confirmed_tags(
        self,
        observed: dict,
        *,
        entity_tags: list[str],
        field_tags: dict[str, list[str]],
    ) -> None:
        observed_entity = {
            item.get("tagFQN")
            for item in observed.get("entity", {}).get("tags", [])
            if item.get("tagFQN") and item.get("state", "Confirmed") == "Confirmed"
        }
        missing_entity = sorted(set(entity_tags) - observed_entity)
        if missing_entity:
            raise ExternalSystemError(
                f"OpenMetadata read-back missing confirmed entity tags: {missing_entity}",
                system="openmetadata",
                retryable=True,
            )

        columns = observed.get("columns", {})
        for field_path, expected in field_tags.items():
            column_name = (
                field_path.split(".", 1)[1]
                if field_path.startswith("columns.")
                else field_path
            )
            observed_tags = {
                item.get("tagFQN")
                for item in columns.get(column_name, {}).get("tags", [])
                if item.get("tagFQN") and item.get("state", "Confirmed") == "Confirmed"
            }
            missing = sorted(set(expected) - observed_tags)
            if missing:
                raise ExternalSystemError(
                    f"OpenMetadata read-back missing confirmed tags on {column_name}: {missing}",
                    system="openmetadata",
                    retryable=True,
                )

    @staticmethod
    def _confirmed_tag_fqns(labels: Any) -> list[str]:
        confirmed: set[str] = set()
        for item in labels or []:
            if not isinstance(item, dict):
                continue
            tag_fqn = str(item.get("tagFQN") or "").strip()
            state = str(item.get("state") or "Confirmed")
            if tag_fqn and state.lower() == "confirmed":
                confirmed.add(tag_fqn)
        return sorted(confirmed)

    def _merge_entity_tags(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        tags: list[str],
        label_type: str,
    ) -> None:
        entity = self.get_entity(
            entity_type=entity_type,
            fqn=entity_fqn,
            fields="tags",
        )
        entity_id = entity.get("id")
        if not entity_id:
            raise ExternalSystemError(
                "OpenMetadata entity response did not contain id",
                system="openmetadata",
                retryable=False,
            )

        current = list(entity.get("tags", []) or [])
        existing = {item.get("tagFQN") for item in current if item.get("tagFQN")}
        additions = [
            self.tag_label(tag, label_type=label_type, state="Confirmed")
            for tag in sorted(set(tags) - existing)
        ]
        if not additions:
            return

        patch = [{"op": "add", "path": "/tags", "value": current + additions}]
        collection = self.collection_for(entity_type)
        self._request(
            "PATCH",
            f"/v1/{collection}/{entity_id}",
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )

    def _merge_column_tags(
        self,
        *,
        column_fqn: str,
        entity_type: str,
        tags: list[str],
        label_type: str,
    ) -> dict:
        column = self.get_column(
            column_fqn=column_fqn,
            entity_type=entity_type,
            fields="tags",
        )
        current = list(column.get("tags", []) or [])
        existing = {item.get("tagFQN") for item in current if item.get("tagFQN")}
        additions = [
            self.tag_label(tag, label_type=label_type, state="Confirmed")
            for tag in sorted(set(tags) - existing)
        ]
        if additions:
            encoded = quote(column_fqn, safe="")
            self._request(
                "PUT",
                f"/v1/columns/name/{encoded}",
                params={"entityType": entity_type},
                json={"tags": current + additions},
            )
        return self.get_column(
            column_fqn=column_fqn,
            entity_type=entity_type,
            fields="tags",
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ExternalSystemError(
                "OpenMetadata request timed out",
                system="openmetadata",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalSystemError(
                "OpenMetadata connection failed",
                system="openmetadata",
                retryable=True,
            ) from exc

        if response.status_code == 404:
            raise NotFoundError(f"OpenMetadata resource not found: {path}")
        if response.is_error:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise ExternalSystemError(
                f"OpenMetadata returned HTTP {response.status_code}: {response.text[:500]}",
                system="openmetadata",
                retryable=retryable,
                status_code=response.status_code,
            )
        return response.json() if response.content else {}
