from __future__ import annotations

from sqlalchemy import select

from app.core.errors import ValidationError
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
        self.validated_tags: list[str] = []
        self.current_tags = {"entity_tags": [], "field_tags": {}}

    def validate_tag_fqns(self, tag_fqns: list[str]) -> None:
        self.validated_tags = tag_fqns

    def get_suggested_or_confirmed_tag_snapshot(self, **_kwargs):
        return self.current_tags

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
    assert fake.validated_tags == ["PII.Email", "Sensitivity.Confidential"]


def test_suggestion_service_skips_live_suggested_and_confirmed_tags(session) -> None:
    with session.begin():
        run = ClassificationRunRepository(session).create(
            event_id="evt-live-tags",
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
    fake.current_tags = {
        "entity_tags": ["Sensitivity.Confidential"],
        "field_tags": {
            "columns.email": ["PII.Email"],
            "columns.phone": ["PII.Phone"],
        },
    }
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
                {"tag": "PII.Email", "field_path": "columns.email"},
                {"tag": "PII.Phone", "field_path": "columns.phone"},
                {"tag": "PII.Name", "field_path": "columns.name"},
                {"tag": "Sensitivity.Confidential", "field_path": None},
            ],
            correlation_id="corr",
        )

    assert result["count"] == 1
    assert fake.calls[0]["field_path"] == "columns.name"
    assert fake.calls[0]["tags"] == ["PII.Name"]


def test_suggestion_service_validates_batch_before_creating_anything(session) -> None:
    class MissingTagOpenMetadata(FakeOpenMetadata):
        def validate_tag_fqns(self, _tag_fqns: list[str]) -> None:
            raise ValidationError("Missing OpenMetadata tags:\n- PII.Address")

    fake = MissingTagOpenMetadata()
    with session.begin():
        try:
            OpenMetadataSuggestionService(
                session,
                fake,
                bot_name="governance-execution-bot",
            ).create(
                classification_run_id="00000000-0000-0000-0000-000000000001",
                entity_type="table",
                entity_fqn="hive.sales.customers",
                source_kind=ClassificationSource.DETERMINISTIC.value,
                source_version="rules-v1",
                suggestions=[
                    {"tag": "PII.Email", "field_path": "columns.email"},
                    {"tag": "PII.Address", "field_path": "columns.address"},
                ],
                correlation_id="corr",
            )
        except ValidationError as exc:
            assert str(exc) == "Missing OpenMetadata tags:\n- PII.Address"
        else:
            raise AssertionError("expected missing taxonomy to stop the batch")

    assert fake.calls == []


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
