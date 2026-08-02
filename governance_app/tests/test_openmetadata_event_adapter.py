from __future__ import annotations

from sqlalchemy import select

from app.core.config import Settings
from app.models.enums import JobType
from app.models.job import GovernanceJob
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_tag_change_enqueues_live_ranger_refresh_without_reclassification(session) -> None:
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
                    "tags": [
                        {
                            "tagFQN": "PII.Email",
                            "state": "Confirmed",
                        }
                    ],
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
    assert jobs[0].job_type == JobType.RECONCILE_RANGER.value
    assert jobs[0].payload == {
        "entity_type": "table",
        "entity_fqn": "hive.sales.customers",
        "refresh_confirmed_tags": True,
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
            "columns": [
                {
                    "name": "email",
                    "dataType": "VARCHAR",
                }
            ],
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
    assert jobs[0].payload["entity_fqn"] == "hive.sales.customers"


def test_tag_payload_nested_under_columns_is_detected(session) -> None:
    event = {
        "id": "evt-nested-column-tags",
        "eventType": "ENTITY_FIELDS_CHANGED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "changeDescription": {
            "fieldsUpdated": [
                {
                    "name": "columns",
                    "newValue": {
                        "name": "mobile_phone",
                        "tags": [
                            {
                                "tagFQN": "PII.Phone",
                                "state": "Confirmed",
                            }
                        ],
                    },
                }
            ]
        },
        "entity": {"name": "customers"},
    }

    with session.begin():
        OpenMetadataEventAdapterService(
            session,
            _settings(),
        ).process_change_event(event)

    job = session.scalar(select(GovernanceJob))

    assert job is not None
    assert job.job_type == JobType.RECONCILE_RANGER.value
    assert job.payload["refresh_confirmed_tags"] is True
