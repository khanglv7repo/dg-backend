from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.clients.sample_query import SampleQueryClient
from app.core.config import Settings
from app.models.enums import ClassificationSource, JobType
from app.repositories.audit import AuditRepository
from app.repositories.classification import ClassificationRunRepository
from app.repositories.data_value_scan import DataValueScanRepository
from app.repositories.jobs import JobRepository
from app.rules.classification import ClassificationRuleEngine
from app.rules.value_detectors import DataValueDetectorEngine
from app.schemas.classification import TagSuggestion

logger = logging.getLogger(__name__)


class DataValueScannerService:
    """Bounded sample-value scanner for unclassified asset columns.

    Evaluates bounded sample values against rule patterns.
    Outputs metrics ONLY to persistence (never raw sample data).
    Sample-based classifications always yield native OpenMetadata Suggestions.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        sample_client: SampleQueryClient | None = None,
        om_client: OpenMetadataClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.sample_client = sample_client or SampleQueryClient(settings, om_client)
        self.audit = AuditRepository(session)
        self.scan_repo = DataValueScanRepository(session)
        self.run_repo = ClassificationRunRepository(session)
        self.jobs = JobRepository(session)

    def scan(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        fields: list[dict[str, Any]],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.sample_scan_enabled:
            return {"status": "SKIPPED", "reason": "sample_scan_enabled is False"}

        config_path = self.settings.resolve_path(self.settings.data_value_scan_config_path)
        detector_engine = DataValueDetectorEngine.from_path(config_path)

        all_suggestions: list[TagSuggestion] = []
        scanned_columns = 0

        for f in fields:
            col_name = f.get("name")
            data_type = str(f.get("data_type") or "").lower()

            if not col_name:
                continue

            # Only sample string/varchar/text columns
            if data_type and not any(t in data_type for t in ("string", "char", "text", "varchar")):
                continue

            field_path = f"columns.{col_name}"

            # Fetch bounded sample values (up to max_rows)
            samples = self.sample_client.fetch_column_samples(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                column_name=col_name,
                max_rows=self.settings.sample_scan_max_rows,
            )
            if not samples:
                continue

            scanned_columns += 1

            # Run detectors on sample values
            suggestions, metrics = detector_engine.scan_column_samples(field_path, samples)

            # Compute input fingerprint from count + length metrics (NO RAW VALUES)
            fingerprint_material = f"{entity_fqn}:{col_name}:{len(samples)}:{detector_engine.configuration_version}"
            input_fingerprint = hashlib.sha256(fingerprint_material.encode()).hexdigest()

            # Record scan run in database (aggregate metrics ONLY, zero raw values stored)
            scan_run = self.scan_repo.create(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                field_path=field_path,
                scanner_version=detector_engine.configuration_version,
                input_fingerprint=input_fingerprint,
                total_samples=len(samples),
                matched_samples=sum(
                    m.get("matched", 0) for m in metrics.get("detectors", {}).values()
                ),
                confidence=min((s.confidence for s in suggestions), default=None),
                metrics=metrics,
                suggestions=[s.model_dump(mode="json") for s in suggestions],
                status="COMPLETED",
                correlation_id=correlation_id,
            )

            all_suggestions.extend(suggestions)

        if not all_suggestions:
            return {
                "status": "COMPLETED",
                "scanned_columns": scanned_columns,
                "suggestions_created": 0,
            }

        # Create ClassificationRun record
        source_version = f"sample-scanner:{detector_engine.configuration_version}"
        run = self.run_repo.create(
            event_id=f"sample-scan-{entity_fqn}",
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            source_kind=ClassificationSource.VALUE_SCANNER.value,
            source_version=source_version,
            outcome="EXACT",
            action="OPENMETADATA_SUGGESTION",
            suggestions=[s.model_dump(mode="json") for s in all_suggestions],
            evidence={"scanned_columns": scanned_columns, "sample_scan": True},
            confidence=min((s.confidence for s in all_suggestions), default=None),
            correlation_id=correlation_id,
        )

        # Enqueue CREATE_OM_SUGGESTIONS job (Sample-based results ALWAYS create Suggestions, NEVER auto-apply)
        entity_tags = [s.tag for s in all_suggestions if not s.field_path]
        field_tags: dict[str, list[str]] = {}
        for s in all_suggestions:
            if s.field_path:
                field_tags.setdefault(s.field_path, []).append(s.tag)

        payload = {
            "entity_tags": sorted(set(entity_tags)),
            "field_tags": {k: sorted(set(v)) for k, v in field_tags.items()},
            "classification_run_id": str(run.id),
            "entity_type": entity_type,
            "entity_fqn": entity_fqn,
            "source_kind": ClassificationSource.VALUE_SCANNER.value,
            "source_version": source_version,
            "suggestions": [s.model_dump(mode="json") for s in all_suggestions],
            "correlation_id": correlation_id,
        }

        key_mat = f"{run.id}|sample-suggestions|{payload}"
        key = hashlib.sha256(key_mat.encode()).hexdigest()
        job = self.jobs.enqueue(
            job_type=JobType.CREATE_OM_SUGGESTIONS,
            idempotency_key=f"sample-suggestions:{key}",
            payload=payload,
            correlation_id=correlation_id,
        )

        self.audit.record(
            actor_id="system:data-value-scanner",
            actor_name="Data Value Scanner",
            action="SAMPLE_VALUE_SCAN_COMPLETED",
            object_type=entity_type,
            object_id=entity_fqn,
            correlation_id=correlation_id,
            details={
                "scanned_columns": scanned_columns,
                "suggestions_count": len(all_suggestions),
                "next_job_id": str(job.id),
            },
        )

        return {
            "status": "COMPLETED",
            "scanned_columns": scanned_columns,
            "suggestions_created": len(all_suggestions),
            "run_id": str(run.id),
            "job_id": str(job.id),
        }
