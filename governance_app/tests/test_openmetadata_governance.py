from __future__ import annotations

from sqlalchemy import select

from app.models.enums import ClassificationAction, ClassificationSource, JobType
from app.models.job import GovernanceJob
from app.repositories.classification import ClassificationRunRepository
from app.services.openmetadata_governance import (
    ConfirmedTagApplicationService,
    OpenMetadataSuggestionService,
)


class FakeOpenMetadata:
    def __init__(self) -> None:
        self.calls = []

    def find_open_tag_suggestion(self, **kwargs):
        return None

    def create_tag_suggestion(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": f"suggestion-{len(self.calls)}"}


class FakeConfirmedOpenMetadata:
    def __init__(self) -> None:
        self.applied: dict | None = None
        self.asserted: dict | None = None

    def apply_confirmed_tags(self, **kwargs):
        self.applied = kwargs
        return {
            "entity": {"tags": []},
            "columns": {
                "email": {
                    "tags": [
                        {
                            "tagFQN": "PII.Email",
                            "state": "Confirmed",
                        }
                    ]
                }
            },
        }

    def assert_confirmed_tags(self, observed, **kwargs):
        self.asserted = {"observed": observed, **kwargs}


def test_suggestion_service_groups_by_openmetadata_entity_link_target(session) -> None:
    with session.begin():
        run = ClassificationRunRepository(session).create(
            event_id="evt-1",
            entity_type="table",
            entity_fqn="hive.sales.customers",
            source_kind=ClassificationSource.DETERMINISTIC.value,
            source_version="rules-v1",
            outcome="EXACT",
            action=ClassificationAction.OPENMETADATA_SUGGESTION.value,
            suggestions=[],
            evidence={},
            confidence=0.94,
            correlation_id="corr",
        )

    fake = FakeOpenMetadata()
    with session.begin():
        result = OpenMetadataSuggestionService(
            session,
            fake,
            bot_name="governance-execution-bot",
        ).create(
            classification_run_id=str(run.id),
            entity_type="table",
            entity_fqn="hive.sales.customers",
            source_kind=ClassificationSource.DETERMINISTIC.value,
            source_version="rules-v1",
            suggestions=[
                {
                    "tag": "PII.Email",
                    "field_path": "columns.email",
                    "rationale": "rule",
                },
                {
                    "tag": "Sensitivity.Confidential",
                    "field_path": None,
                    "rationale": "rule",
                },
            ],
            correlation_id="corr",
        )

    session.refresh(run)
    assert result["count"] == 2
    assert run.openmetadata_suggestion_ids == ["suggestion-1", "suggestion-2"]
    assert {call["field_path"] for call in fake.calls} == {
        None,
        "columns.email",
    }


def test_trusted_auto_apply_enqueues_ranger_tag_sync(session) -> None:
    fake = FakeConfirmedOpenMetadata()

    with session.begin():
        result = ConfirmedTagApplicationService(
            session,
            fake,
            bot_name="governance-execution-bot",
        ).apply(
            classification_run_id="run-1",
            entity_type="table",
            entity_fqn="hive.sales.customers",
            entity_tags=[],
            field_tags={"columns.email": ["PII.Email"]},
            correlation_id="corr",
        )

    job = session.scalar(select(GovernanceJob))

    assert fake.applied is not None
    assert fake.asserted is not None
    assert job is not None
    assert job.job_type == JobType.SYNC_RANGER_TAGS.value
    assert job.payload == {
        "entity_type": "table",
        "entity_fqn": "hive.sales.customers",
        "classification_run_id": "run-1",
        "correlation_id": "corr",
    }
    assert result["tag_sync_job_id"] == str(job.id)
