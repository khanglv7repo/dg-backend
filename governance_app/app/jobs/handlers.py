from __future__ import annotations

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.clients.trino import TrinoDBAPIExecutor
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.models.enums import JobType
from app.schemas.events import MetadataEventRequest
from app.services.asset_discovery import AssetDiscoveryService
from app.services.classification import ClassificationService
from app.services.classification_commands import OpenMetadataClassificationRunner
from app.services.data_value_scanner import DataValueScannerService
from app.services.openmetadata_governance import (
    ConfirmedTagApplicationService,
    OpenMetadataSuggestionService,
)
from app.services.policy_sync import (
    RangerPolicyCatalogSyncService,
    RangerTagAssignmentService,
)
from app.services.verification import VerificationService


def _autoclassification_openmetadata_client(
    settings: Settings,
) -> OpenMetadataClient:
    if not settings.openmetadata_enabled:
        raise ConfigurationError("OpenMetadata integration is disabled")
    return OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=(
            settings.openmetadata_execution_bot_token.get_secret_value()
            if settings.openmetadata_execution_bot_token
            else None
        ),
        timeout=settings.openmetadata_timeout_seconds,
    )


def handle_classify(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    return ClassificationService(session, settings).classify(
        MetadataEventRequest.model_validate(payload)
    )


def handle_classify_from_openmetadata(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    client = _autoclassification_openmetadata_client(settings)
    try:
        return OpenMetadataClassificationRunner(
            session,
            settings,
            client,
        ).run(payload)
    finally:
        client.close()


def handle_create_om_suggestions(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    return OpenMetadataSuggestionService(
        session,
        _auto_tag_openmetadata_client(settings),
        bot_name=settings.openmetadata_execution_bot_name,
    ).create(
        classification_run_id=payload["classification_run_id"],
        entity_type=payload["entity_type"],
        entity_fqn=payload["entity_fqn"],
        source_kind=payload["source_kind"],
        source_version=payload["source_version"],
        suggestions=list(payload.get("suggestions", [])),
        correlation_id=payload.get("correlation_id"),
    )


def handle_apply_confirmed_tags(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    return ConfirmedTagApplicationService(
        session,
        _auto_tag_openmetadata_client(settings),
        bot_name=settings.openmetadata_execution_bot_name,
    ).apply(
        classification_run_id=payload.get("classification_run_id"),
        entity_type=payload["entity_type"],
        entity_fqn=payload["entity_fqn"],
        entity_tags=list(payload.get("entity_tags", [])),
        field_tags=dict(payload.get("field_tags", {})),
        correlation_id=payload.get("correlation_id"),
    )


def handle_sync_ranger_policies(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    if not settings.ranger_enabled:
        raise ConfigurationError("Ranger integration is disabled")

    services = {
        settings.ranger_tag_service_name,
        settings.ranger_service_name,
    }
    clients = {
        service: _ranger_policy_client(settings, service)
        for service in services
    }
    tag_store = _ranger_tag_store_client(settings)
    try:
        return RangerPolicyCatalogSyncService(
            session,
            settings,
            clients,
            tag_store,
        ).sync(
            policy_ids=[str(value) for value in payload.get("policy_ids", [])],
            correlation_id=payload.get("correlation_id"),
        )
    finally:
        tag_store.close()
        for client in clients.values():
            client.close()


def handle_sync_ranger_tags(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    if not settings.ranger_enabled:
        raise ConfigurationError("Ranger integration is disabled")
    if not settings.openmetadata_enabled:
        raise ConfigurationError(
            "OpenMetadata integration must be enabled to sync Ranger tags"
        )

    entity_type = str(payload.get("entity_type") or "table")
    entity_fqn = str(payload["entity_fqn"])

    om_client = _auto_tag_openmetadata_client(settings)
    try:
        snapshot = om_client.get_confirmed_tag_snapshot(
            entity_type=entity_type,
            entity_fqn=entity_fqn,
        )
    finally:
        om_client.close()

    tag_store = _ranger_tag_store_client(settings)
    try:
        return RangerTagAssignmentService(
            session,
            settings,
            tag_store,
        ).sync(
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            entity_tags=list(snapshot["entity_tags"]),
            field_tags=dict(snapshot["field_tags"]),
            classification_run_id=payload.get("classification_run_id"),
            correlation_id=payload.get("correlation_id"),
        )
    finally:
        tag_store.close()


def handle_reconcile_ranger(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    """Compatibility handler for already-queued v0.4 RECONCILE_RANGER jobs."""
    return handle_sync_ranger_tags(session, settings, payload)


def handle_verify_trino(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    if not settings.trino_enabled:
        raise ConfigurationError("Trino integration is disabled")
    executor = TrinoDBAPIExecutor(
        host=settings.trino_host,
        port=settings.trino_port,
        catalog=settings.trino_catalog,
        schema=settings.trino_schema,
        http_scheme=settings.trino_http_scheme,
        timeout_seconds=settings.trino_timeout_seconds,
    )
    return VerificationService(session, executor).verify(
        verification_group_id=payload["verification_group_id"],
        verification_total=int(payload["verification_total"]),
        policy_key=payload["policy_key"],
        identity=payload["identity"],
        sql=payload["sql"],
        expected_allowed=bool(payload["expected_allowed"]),
        classification_run_id=payload.get("classification_run_id"),
        correlation_id=payload.get("correlation_id"),
    )


def handle_discover_unclassified_assets(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    client = (
        _ingestion_openmetadata_client(settings)
        if settings.openmetadata_enabled
        else None
    )
    return AssetDiscoveryService(session, settings, client).discover(
        correlation_id=payload.get("correlation_id")
    )


def handle_sample_column_values(
    session: Session,
    settings: Settings,
    payload: dict,
) -> dict:
    om_client = (
        _autoclassification_openmetadata_client(settings)
        if settings.openmetadata_enabled
        else None
    )
    return DataValueScannerService(
        session,
        settings,
        om_client=om_client,
    ).scan(
        entity_type=payload.get("entity_type", "table"),
        entity_fqn=payload["entity_fqn"],
        fields=list(payload.get("fields", [])),
        correlation_id=payload.get("correlation_id"),
    )


def _ingestion_openmetadata_client(
    settings: Settings,
) -> OpenMetadataClient:
    if not settings.openmetadata_enabled:
        raise ConfigurationError("OpenMetadata integration is disabled")
    return OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=(
            settings.openmetadata_ingestion_bot_token.get_secret_value()
            if settings.openmetadata_ingestion_bot_token
            else None
        ),
        timeout=settings.openmetadata_timeout_seconds,
    )


def _auto_tag_openmetadata_client(
    settings: Settings,
) -> OpenMetadataClient:
    if not settings.openmetadata_enabled:
        raise ConfigurationError("OpenMetadata integration is disabled")
    return OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=(
            settings.openmetadata_auto_tag_bot_token.get_secret_value()
            if settings.openmetadata_auto_tag_bot_token
            else None
        ),
        timeout=settings.openmetadata_timeout_seconds,
    )


def _ranger_policy_client(settings: Settings, service_name: str) -> RangerClient:
    return RangerClient(
        base_url=settings.ranger_base_url,
        username=settings.ranger_service_account,
        password=(
            settings.ranger_service_secret.get_secret_value()
            if settings.ranger_service_secret
            else None
        ),
        service_name=service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )


def _ranger_tag_store_client(
    settings: Settings,
) -> RangerTagStoreClient:
    return RangerTagStoreClient(
        base_url=settings.ranger_tag_store_base_url,
        username=settings.ranger_service_account,
        password=(
            settings.ranger_service_secret.get_secret_value()
            if settings.ranger_service_secret
            else None
        ),
        resource_service_name=settings.ranger_service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )


HANDLERS = {
    JobType.CLASSIFY_ASSET: handle_classify,
    JobType.CLASSIFY_ASSET_FROM_OM: handle_classify_from_openmetadata,
    JobType.CREATE_OM_SUGGESTIONS: handle_create_om_suggestions,
    JobType.APPLY_CONFIRMED_TAGS: handle_apply_confirmed_tags,
    JobType.SYNC_RANGER_POLICIES: handle_sync_ranger_policies,
    JobType.SYNC_RANGER_TAGS: handle_sync_ranger_tags,
    JobType.RECONCILE_RANGER: handle_reconcile_ranger,
    JobType.VERIFY_TRINO: handle_verify_trino,
    JobType.DISCOVER_UNCLASSIFIED_ASSETS: handle_discover_unclassified_assets,
    JobType.SAMPLE_COLUMN_VALUES: handle_sample_column_values,
}
