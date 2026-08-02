
from app.models.enums import ClassificationAction, ClassificationSource
from app.repositories.classification import ClassificationRunRepository
from app.services.openmetadata_governance import OpenMetadataSuggestionService


class FakeOpenMetadata:
    def __init__(self) -> None:
        self.calls = []

    def find_open_tag_suggestion(self, **kwargs):
        return None

    def create_tag_suggestion(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": f"suggestion-{len(self.calls)}"}


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
        result = OpenMetadataSuggestionService(session, fake, bot_name="governance-execution-bot").create(
            classification_run_id=str(run.id),
            entity_type="table",
            entity_fqn="hive.sales.customers",
            source_kind=ClassificationSource.DETERMINISTIC.value,
            source_version="rules-v1",
            suggestions=[
                {"tag": "PII.Email", "field_path": "columns.email", "rationale": "rule"},
                {"tag": "Sensitivity.Confidential", "field_path": None, "rationale": "rule"},
            ],
            correlation_id="corr",
        )

    session.refresh(run)
    assert result["count"] == 2
    assert run.openmetadata_suggestion_ids == ["suggestion-1", "suggestion-2"]
    assert {call["field_path"] for call in fake.calls} == {None, "columns.email"}

class ExistingSuggestionOpenMetadata(FakeOpenMetadata):
    def find_open_tag_suggestion(self, **kwargs):
        return {"id": "existing-suggestion", "description": kwargs["marker"]}


def test_suggestion_service_reuses_open_suggestion_on_retry(session) -> None:
    with session.begin():
        run = ClassificationRunRepository(session).create(
            event_id="evt-retry",
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

    fake = ExistingSuggestionOpenMetadata()
    with session.begin():
        result = OpenMetadataSuggestionService(session, fake, bot_name="governance-execution-bot").create(
            classification_run_id=str(run.id),
            entity_type="table",
            entity_fqn="hive.sales.customers",
            source_kind=ClassificationSource.DETERMINISTIC.value,
            source_version="rules-v1",
            suggestions=[
                {"tag": "PII.Email", "field_path": "columns.email", "rationale": "rule"}
            ],
            correlation_id="corr",
        )

    assert result["suggestion_ids"] == ["existing-suggestion"]
    assert fake.calls == []
