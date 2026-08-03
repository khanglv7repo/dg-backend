from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.core.errors import ExternalSystemError
from app.models.enums import ClassificationSource, JobType
from app.repositories.audit import AuditRepository
from app.repositories.classification import ClassificationRunRepository
from app.repositories.jobs import JobRepository


class OpenMetadataSuggestionService:
    def __init__(
        self,
        session: Session,
        client: OpenMetadataClient,
        *,
        bot_name: str,
    ) -> None:
        self.session = session
        self.client = client
        self.bot_name = bot_name
        self.audit = AuditRepository(session)
        self.runs = ClassificationRunRepository(session)

    def create(
        self,
        *,
        classification_run_id: str,
        entity_type: str,
        entity_fqn: str,
        source_kind: str,
        source_version: str,
        suggestions: list[dict],
        correlation_id: str | None,
    ) -> dict:
        grouped: dict[str | None, list[dict]] = {}
        for item in suggestions:
            grouped.setdefault(item.get("field_path"), []).append(item)

        label_type = (
            "Generated"
            if source_kind == ClassificationSource.AGENT.value
            else "Automated"
        )
        suggestion_ids: list[str] = []
        for field_path, items in sorted(
            grouped.items(),
            key=lambda pair: pair[0] or "",
        ):
            tags = sorted({str(item["tag"]) for item in items})
            evidence = "; ".join(
                f"{item.get('tag')}: "
                f"{item.get('rationale', 'classification evidence')}"
                for item in items
            )
            marker_material = (
                f"{classification_run_id}|{field_path or '$entity'}|{tags}"
            )
            marker = (
                "[dg-classification:"
                f"{hashlib.sha256(marker_material.encode()).hexdigest()[:20]}]"
            )
            response = self.client.find_open_tag_suggestion(
                entity_fqn=entity_fqn,
                marker=marker,
            )
            if response is None:
                response = self.client.create_tag_suggestion(
                    entity_type=entity_type,
                    entity_fqn=entity_fqn,
                    field_path=field_path,
                    tags=tags,
                    description=(
                        f"{marker} {source_kind} classification proposal "
                        f"({source_version}). {evidence}"
                    ),
                    label_type=label_type,
                )
            suggestion_id = response.get("id")
            if not suggestion_id:
                raise ExternalSystemError(
                    "OpenMetadata Suggestion response did not contain id",
                    system="openmetadata",
                    retryable=False,
                )
            suggestion_ids.append(str(suggestion_id))

        self.runs.set_openmetadata_suggestions(
            classification_run_id,
            suggestion_ids,
        )
        self.audit.record(
            actor_id=f"bot:{self.bot_name}",
            actor_name=self.bot_name,
            action="OPENMETADATA_SUGGESTIONS_CREATED",
            object_type=entity_type,
            object_id=entity_fqn,
            correlation_id=correlation_id,
            details={
                "classification_run_id": classification_run_id,
                "suggestion_ids": suggestion_ids,
                "source_kind": source_kind,
            },
        )
        return {
            "suggestion_ids": suggestion_ids,
            "count": len(suggestion_ids),
        }


class ConfirmedTagApplicationService:
    """Trusted direct OM tag application for the AUTO_APPLY branch.

    AUTO_APPLY and human confirmation converge on the same Flow-B boundary:
    current Confirmed OpenMetadata state -> Ranger tag assignments.
    """

    def __init__(
        self,
        session: Session,
        client: OpenMetadataClient,
        *,
        bot_name: str,
    ) -> None:
        self.session = session
        self.client = client
        self.bot_name = bot_name
        self.audit = AuditRepository(session)

    def apply(
        self,
        *,
        classification_run_id: str | None,
        entity_type: str,
        entity_fqn: str,
        entity_tags: list[str],
        field_tags: dict[str, list[str]],
        correlation_id: str | None,
    ) -> dict:
        observed = self.client.apply_confirmed_tags(
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            entity_tags=entity_tags,
            field_tags=field_tags,
            label_type="Automated",
        )
        self.client.assert_confirmed_tags(
            observed,
            entity_tags=entity_tags,
            field_tags=field_tags,
        )

        logical = json.dumps(
            {
                "entity_type": entity_type,
                "entity_fqn": entity_fqn,
                "entity_tags": sorted(set(entity_tags)),
                "field_tags": {
                    key: sorted(set(values))
                    for key, values in sorted(field_tags.items())
                },
                "classification_run_id": classification_run_id,
                "purpose": "sync-ranger-tag-assignments",
            },
            sort_keys=True,
        )
        key = hashlib.sha256(logical.encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=JobType.SYNC_RANGER_TAGS,
            idempotency_key=f"sync-ranger-tags:{key}",
            payload={
                "entity_type": entity_type,
                "entity_fqn": entity_fqn,
                "classification_run_id": classification_run_id,
                "correlation_id": correlation_id,
            },
            correlation_id=correlation_id,
            max_attempts=5,
        )
        self.audit.record(
            actor_id=f"bot:{self.bot_name}",
            actor_name=self.bot_name,
            action="OPENMETADATA_CONFIRMED_TAGS_APPLIED",
            object_type=entity_type,
            object_id=entity_fqn,
            correlation_id=correlation_id,
            details={
                "classification_run_id": classification_run_id,
                "entity_tags": entity_tags,
                "field_tags": field_tags,
                "next_job_id": str(job.id),
                "snapshot_source": "openmetadata-readback",
            },
        )
        return {
            "tag_sync_job_id": str(job.id),
            # Compatibility key for callers written against the previous patch.
            "reconcile_job_id": str(job.id),
        }
