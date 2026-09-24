from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import ExternalSystemError, NotFoundError, ValidationError


class OpenMetadataClient:
    """Narrow OpenMetadata 1.13 adapter.

    OpenMetadata remains the metadata and review system of record. Ranger
    reconciliation always reads the current Confirmed tag state from this
    adapter instead of trusting a possibly partial ChangeEvent payload.
    """

    def __init__(self, *, base_url: str, token: str | None, timeout: float = 15.0) -> None:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            raw_token = token.get_secret_value() if hasattr(token, "get_secret_value") else str(token)
            headers["Authorization"] = f"Bearer {raw_token}"
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

    def create_test_case(
        self,
        *,
        name: str,
        entity_link: str,
        test_definition_fqn: str,
        parameter_values: dict[str, Any],
    ) -> dict:
        """POST /api/v1/dataQuality/testCases using the observed REST shape
        (planning/evidence/TASK-04/b1_create_test_case_response.json), NOT
        the MCP tool's fqn+columnName shape -- the two differ
        (docs/13_IMPLEMENTATION_SPEC.md section 5). No explicit testSuite is
        passed: OM auto-attaches a `basic` (logical, non-executable) suite,
        which is the confirmed B2 STAGED mechanism.
        """
        body = {
            "name": name,
            "entityLink": entity_link,
            "testDefinition": test_definition_fqn,
            "parameterValues": [
                {"name": key, "value": value} for key, value in parameter_values.items()
            ],
        }
        return self._request("POST", "/v1/dataQuality/testCases", json=body)

    def get_test_suite_by_name(self, fqn: str) -> dict:
        """Read one TestSuite by FQN to verify its executable/basic semantics."""
        encoded = quote(fqn, safe="")
        return self._request(
            "GET",
            f"/v1/dataQuality/testSuites/name/{encoded}",
            params={"fields": "tests"},
        )

    def get_test_case_by_name(
        self,
        fqn: str,
        *,
        fields: str = "testSuite,testDefinition",
    ) -> dict | None:
        """Deterministic lookup by name/FQN, used for crash-recovery
        reconciliation (I1) -- does not raise on 404, returns None instead,
        since "not found" is an expected outcome during recovery.
        """
        encoded = quote(fqn, safe="")
        try:
            return self._request(
                "GET",
                f"/v1/dataQuality/testCases/name/{encoded}",
                params={"fields": fields},
            )
        except NotFoundError:
            return None

    def find_test_case_by_entity_and_name(
        self,
        *,
        entity_fqn: str,
        name: str,
    ) -> dict | None:
        """Find one TestCase using OM 2.0.2's entityFQN list filter.

        This avoids reconstructing OpenMetadata FQN quoting rules during
        crash recovery. The deterministic TestCase name is the idempotency key.
        """
        response = self._request(
            "GET",
            "/v1/dataQuality/testCases",
            params={
                "entityFQN": entity_fqn,
                "includeAllTests": "true",
                "fields": "testSuite,testDefinition",
                "limit": 1000,
            },
        )
        matches = [
            item
            for item in response.get("data", []) or []
            if isinstance(item, dict) and str(item.get("name") or "") == name
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ExternalSystemError(
                f"OpenMetadata returned duplicate TestCases named {name!r} for {entity_fqn!r}",
                system="openmetadata",
                retryable=False,
            )
        return matches[0]

    def get_task(self, task_id: str) -> dict:
        """GET /api/v1/tasks/{id} -- the unified OM 2.0.2 Task entity, NOT
        the legacy /v1/feed/tasks threads. Always re-fetched rather than
        trusted from a ChangeEvent payload, since task.entityUpdated events
        never include the status field itself
        (docs/13_IMPLEMENTATION_SPEC.md section 5, Hard Invariant #20).
        """
        return self._request("GET", f"/v1/tasks/{task_id}")

    def get_tag(self, tag_fqn: str) -> dict:
        """Return the taxonomy tag identified by its OpenMetadata FQN."""
        return self._request(
            "GET",
            f"/v1/tags/name/{quote(tag_fqn, safe='')}",
        )

    def tag_exists(self, tag_fqn: str) -> bool:
        try:
            self.get_tag(tag_fqn)
        except NotFoundError:
            return False
        return True

    def validate_tag_fqns(self, tag_fqns: list[str]) -> None:
        """Fail a suggestion batch before any write when taxonomy is incomplete."""
        missing = [
            tag_fqn
            for tag_fqn in sorted({tag.strip() for tag in tag_fqns if tag.strip()})
            if not self.tag_exists(tag_fqn)
        ]
        if missing:
            raise ValidationError(
                "Missing OpenMetadata tags:\n"
                + "\n".join(f"- {tag_fqn}" for tag_fqn in missing)
            )

    def get_suggested_or_confirmed_tag_snapshot(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
    ) -> dict[str, Any]:
        """Read live tags once for duplicate-free native Suggestions."""
        entity = self.get_entity(
            entity_type=entity_type,
            fqn=entity_fqn,
            fields="tags,columns",
        )
        field_tags: dict[str, list[str]] = {}
        for column in entity.get("columns", []) or []:
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            tags = self._suggested_or_confirmed_tag_fqns(column.get("tags", []))
            if tags:
                field_tags[f"columns.{column_name}"] = tags
        return {
            "entity_tags": self._suggested_or_confirmed_tag_fqns(
                entity.get("tags", [])
            ),
            "field_tags": field_tags,
        }

    def get_column(
    self,
    *,
    column_fqn: str,
    entity_type: str = "table",
    fields: str = "tags",
    ) -> dict:
        if entity_type != "table":
            raise NotFoundError(
                f"Column lookup is only supported for table entities: {column_fqn}"
            )

        table_fqn, separator, column_name = column_fqn.rpartition(".")
        if not separator or not table_fqn or not column_name:
            raise NotFoundError(
                f"Invalid OpenMetadata column FQN: {column_fqn}"
            )

        table = self.get_entity(
            entity_type="table",
            fqn=table_fqn,
            fields=fields,
        )

        for column in table.get("columns", []) or []:
            if not isinstance(column, dict):
                continue

            if (
                column.get("fullyQualifiedName") == column_fqn
                or column.get("name") == column_name
            ):
                return column

        raise NotFoundError(
            f"OpenMetadata column not found: {column_fqn}"
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

    def list_confirmed_table_tag_snapshots(
        self,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read the latest Confirmed tag state for the current lab table scope.

        The R3 TAG_SYNC worker uses this full snapshot instead of replaying
        webhook deltas. OpenMetadata is the source of truth; missing ``state`` is
        accepted as confirmed for deployed OM versions that omit state for
        confirmed assignments, while explicit Suggested labels are excluded.
        """
        snapshots: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            params = {"limit": limit, "fields": "tags,columns"}
            if after:
                params["after"] = after

            response = self._request(
                "GET",
                "/v1/tables",
                params=params,
            )
            for table in response.get("data", []) or []:
                if not isinstance(table, dict):
                    continue
                entity_fqn = str(
                    table.get("fullyQualifiedName") or table.get("name") or ""
                ).strip()
                if not entity_fqn:
                    continue
                snapshots.append(
                    self._confirmed_snapshot_from_entity(
                        entity_type="table",
                        entity_fqn=entity_fqn,
                        entity=table,
                    )
                )

            paging = response.get("paging") or {}
            after = paging.get("after")
            if not after:
                break
        return snapshots

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

    @staticmethod
    def _confirmed_tag_fqns(labels: Any) -> list[str]:
        confirmed: set[str] = set()
        for item in labels or []:
            if not isinstance(item, dict):
                continue
            tag_fqn = str(item.get("tagFQN") or "").strip()
            state = item.get("state")
            state_text = str(state).strip().lower() if state is not None else "confirmed"
            if tag_fqn and state_text == "confirmed":
                confirmed.add(tag_fqn)
        return sorted(confirmed)

    def _confirmed_snapshot_from_entity(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        entity: dict[str, Any],
    ) -> dict[str, Any]:
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

        all_tags = set(entity_tags)
        for values in field_tags.values():
            all_tags.update(values)

        return {
            "entity_type": entity_type,
            "entity_fqn": entity_fqn,
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

    @staticmethod
    def _suggested_or_confirmed_tag_fqns(labels: Any) -> list[str]:
        current: set[str] = set()
        for item in labels or []:
            if not isinstance(item, dict):
                continue
            tag_fqn = str(item.get("tagFQN") or "").strip()
            state = str(item.get("state") or "Confirmed").lower()
            if tag_fqn and state in {"suggested", "confirmed"}:
                current.add(tag_fqn)
        return sorted(current)

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
            detail = response.text.strip()
            if detail:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict):
                    detail = str(
                        body.get("message")
                        or body.get("error")
                        or body.get("detail")
                        or detail
                    )
                raise NotFoundError(
                    f"OpenMetadata returned 404 for {path}: {detail[:500]}"
                )
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
