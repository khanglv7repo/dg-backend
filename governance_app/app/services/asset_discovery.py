from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.core.config import Settings
from app.models.enums import JobType
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.repositories.watermark import IntegrationWatermarkRepository
from app.rules.classification import ClassificationRuleEngine
from app.schemas.events import MetadataEventRequest, MetadataField

logger = logging.getLogger(__name__)


class AssetDiscoveryService:
    """Discovers new/changed unclassified assets using watermark timestamps."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        om_client: OpenMetadataClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.om_client = om_client
        self.watermark_repo = IntegrationWatermarkRepository(session)
        self.jobs = JobRepository(session)
        self.audit = AuditRepository(session)

    def discover(self, correlation_id: str | None = None) -> dict[str, Any]:
        system_name = "openmetadata"
        watermark_key = "asset_discovery_last_timestamp"

        last_watermark = self.watermark_repo.get(system_name, watermark_key)
        start_ts = int(last_watermark) if last_watermark and last_watermark.isdigit() else 0

        engine = ClassificationRuleEngine.from_path(
            self.settings.resolve_path(self.settings.classification_rules_path)
        )

        current_ts = int(time.time() * 1000)

        if not self.om_client or not self.settings.openmetadata_enabled:
            logger.info("OpenMetadata client not available for asset discovery")
            return {"discovered_assets": 0, "status": "SKIPPED"}

        enqueued_jobs = []

        try:
            # Query updated entities from OpenMetadata via GET /v1/tables or search
            # We filter entities updated after start_ts
            response = self.om_client._request(
                "GET",
                "/v1/tables",
                params={"limit": 100, "fields": "tags,columns,updatedAt,version"},
            )
            tables = response.get("data", []) or []

            for table in tables:
                updated_at = table.get("updatedAt", 0)
                if updated_at < start_ts and start_ts > 0:
                    continue

                entity_fqn = table.get("fullyQualifiedName") or table.get("name")
                if not entity_fqn:
                    continue

                version = str(table.get("version", "0.1"))
                columns = table.get("columns", [])
                fields = [
                    MetadataField(
                        name=col.get("name"),
                        data_type=col.get("dataType"),
                        description=col.get("description"),
                    )
                    for col in columns
                    if col.get("name")
                ]

                req = MetadataEventRequest(
                    event_id=f"discovery-{entity_fqn}-{version}",
                    event_type="ENTITY_UPDATED",
                    entity_type="table",
                    entity_fqn=entity_fqn,
                    entity_name=table.get("name", entity_fqn.split(".")[-1]),
                    description=table.get("description"),
                    fields=fields,
                    existing_tags=[
                        t.get("tagFQN")
                        for t in table.get("tags", [])
                        if t.get("tagFQN")
                    ],
                    correlation_id=correlation_id,
                )

                idempotency_key = (
                    f"classify:{entity_fqn}:{version}:{engine.configuration_version}"
                )

                job = self.jobs.enqueue(
                    job_type=JobType.CLASSIFY_ASSET,
                    idempotency_key=idempotency_key,
                    payload=req.model_dump(mode="json"),
                    correlation_id=correlation_id,
                    max_attempts=3,
                )
                enqueued_jobs.append(str(job.id))

            # Update watermark to current_ts
            self.watermark_repo.set(system_name, watermark_key, str(current_ts))

            self.audit.record(
                actor_id="system:asset-discovery",
                actor_name="Asset Discovery Service",
                action="UNCLASSIFIED_ASSETS_DISCOVERED",
                object_type="catalog",
                object_id=system_name,
                correlation_id=correlation_id,
                details={
                    "discovered_count": len(enqueued_jobs),
                    "start_watermark": start_ts,
                    "end_watermark": current_ts,
                },
            )

        except Exception as exc:
            logger.warning(f"Asset discovery query error: {exc}")
            return {"discovered_assets": 0, "status": "ERROR", "error": str(exc)}

        return {
            "discovered_assets": len(enqueued_jobs),
            "enqueued_jobs": enqueued_jobs,
            "status": "COMPLETED",
        }
