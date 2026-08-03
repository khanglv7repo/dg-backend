from __future__ import annotations

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
from app.schemas.events import MetadataEventRequest, MetadataField
from app.services.classification_rule_catalog import (
    ClassificationRuleCatalogService,
)

logger = logging.getLogger(__name__)


class AssetDiscoveryService:
    """Discovers new/changed assets and enqueues classification."""

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

    def discover(
        self,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        # A disabled integration must be a real no-op. It should not require
        # classification configuration just to return SKIPPED.
        if (
            not self.om_client
            or not self.settings.openmetadata_enabled
        ):
            logger.info(
                "OpenMetadata client not available for asset discovery"
            )
            return {
                "discovered_assets": 0,
                "status": "SKIPPED",
            }

        system_name = "openmetadata"
        watermark_key = "asset_discovery_last_timestamp"

        last_watermark = self.watermark_repo.get(
            system_name,
            watermark_key,
        )
        start_ts = (
            int(last_watermark)
            if last_watermark
            and last_watermark.isdigit()
            else 0
        )

        # Only an enabled discovery run needs classification configuration.
        engine = ClassificationRuleCatalogService(
            self.session
        ).active_engine()

        current_ts = int(time.time() * 1000)
        enqueued_jobs: list[str] = []

        try:
            response = self.om_client._request(
                "GET",
                "/v1/tables",
                params={
                    "limit": 100,
                    "fields": (
                        "tags,columns,updatedAt,version"
                    ),
                },
            )
            tables = response.get("data", []) or []

            for table in tables:
                updated_at = table.get("updatedAt", 0)
                if (
                    updated_at < start_ts
                    and start_ts > 0
                ):
                    continue

                entity_fqn = (
                    table.get("fullyQualifiedName")
                    or table.get("name")
                )
                if not entity_fqn:
                    continue

                version = str(
                    table.get("version", "0.1")
                )
                columns = table.get(
                    "columns",
                    [],
                )
                fields = [
                    MetadataField(
                        name=col.get("name"),
                        data_type=col.get("dataType"),
                        description=col.get(
                            "description"
                        ),
                    )
                    for col in columns
                    if col.get("name")
                ]

                req = MetadataEventRequest(
                    event_id=(
                        f"discovery-{entity_fqn}-{version}"
                    ),
                    event_type="ENTITY_UPDATED",
                    entity_type="table",
                    entity_fqn=entity_fqn,
                    entity_name=table.get(
                        "name",
                        entity_fqn.split(".")[-1],
                    ),
                    description=table.get(
                        "description"
                    ),
                    fields=fields,
                    existing_tags=[
                        tag.get("tagFQN")
                        for tag in table.get(
                            "tags",
                            [],
                        )
                        if tag.get("tagFQN")
                    ],
                    correlation_id=correlation_id,
                )

                idempotency_key = (
                    f"classify:{entity_fqn}:{version}:"
                    f"{engine.configuration_version}"
                )

                job = self.jobs.enqueue(
                    job_type=JobType.CLASSIFY_ASSET,
                    idempotency_key=idempotency_key,
                    payload=req.model_dump(
                        mode="json"
                    ),
                    correlation_id=correlation_id,
                    max_attempts=3,
                )
                enqueued_jobs.append(
                    str(job.id)
                )

            self.watermark_repo.set(
                system_name,
                watermark_key,
                str(current_ts),
            )

            self.audit.record(
                actor_id="system:asset-discovery",
                actor_name="Asset Discovery Service",
                action=(
                    "UNCLASSIFIED_ASSETS_DISCOVERED"
                ),
                object_type="catalog",
                object_id=system_name,
                correlation_id=correlation_id,
                details={
                    "discovered_count":
                        len(enqueued_jobs),
                    "start_watermark":
                        start_ts,
                    "end_watermark":
                        current_ts,
                    "classification_rule_version":
                        engine.configuration_version,
                    "classification_rule_sha256":
                        engine.configuration_sha256,
                },
            )

        except Exception as exc:
            logger.warning(
                "Asset discovery query error: %s",
                exc,
            )
            return {
                "discovered_assets": 0,
                "status": "ERROR",
                "error": str(exc),
            }

        return {
            "discovered_assets":
                len(enqueued_jobs),
            "enqueued_jobs":
                enqueued_jobs,
            "status": "COMPLETED",
        }
