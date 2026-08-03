from __future__ import annotations

from sqlalchemy import select

from app.core.config import Settings
from app.models.enums import JobType
from app.models.job import GovernanceJob
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_tag_change_enqueues_tag_sync_without_reclassification(session) -> None:
    event = {
        "id": "evt-confirm-email",
        "eventType": "ENTITY_UPDATED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "timestamp": 123,
        "changeDescription": {
            "fieldsUpdated": [
                {
                    "name": "columns.email.tags",
                    "oldValue": [],
                    "newValue": [
                        {
                            "tagFQN": "PII.Email",
                            "state": "Confirmed",
                        }
                    ],
                }
            ]
        },
        "entity": {
            "name": "customers",
            "columns": [
                {
                    "name": "email",
                    "dataType": "VARCHAR",
                }
            ],
        },
    }

    with session.begin():
        created = OpenMetadataEventAdapterService(
            session,
            _settings(),
        ).process_change_event(event)

    jobs = list(session.scalars(select(GovernanceJob)))

    assert len(created) == 1
    assert len(jobs) == 1
    assert jobs[0].job_type == JobType.SYNC_RANGER_TAGS.value
    assert jobs[0].payload == {
        "entity_type": "table",
        "entity_fqn": "hive.sales.customers",
        "classification_run_id": None,
        "correlation_id": "om-event-evt-confirm-email",
    }


def test_non_tag_metadata_update_still_enqueues_classification(session) -> None:
    event = {
        "id": "evt-description",
        "eventType": "ENTITY_UPDATED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "timestamp": 456,
        "changeDescription": {
            "fieldsUpdated": [
                {
                    "name": "description",
                    "oldValue": "old",
                    "newValue": "new",
                }
            ]
        },
        "entity": {
            "name": "customers",
            "description": "new",
            "columns": [{"name": "email", "dataType": "VARCHAR"}],
        },
    }

    with session.begin():
        OpenMetadataEventAdapterService(
            session,
            _settings(),
        ).process_change_event(event)

    jobs = list(session.scalars(select(GovernanceJob)))

    assert len(jobs) == 1
    assert jobs[0].job_type == JobType.CLASSIFY_ASSET.value
