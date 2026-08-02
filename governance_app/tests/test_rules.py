from pathlib import Path

from app.rules.classification import ClassificationRuleEngine
from app.schemas.classification import MatchOutcome
from app.schemas.events import MetadataEventRequest, MetadataField


def test_exact_email_rule_is_trusted_auto_apply() -> None:
    engine = ClassificationRuleEngine.from_path(Path("config/classification_rules.yaml"))
    event = MetadataEventRequest(
        event_id="evt-1",
        event_type="ENTITY_UPDATED",
        entity_fqn="hive.sales.customers",
        entity_name="customers",
        fields=[MetadataField(name="email", data_type="varchar")],
    )

    result = engine.evaluate(event)

    assert result.outcome == MatchOutcome.EXACT
    assert result.trusted_auto_apply is True
    assert [(item.tag, item.field_path) for item in result.suggestions] == [
        ("PII.Email", "columns.email")
    ]


def test_conflicting_tags_are_ambiguous() -> None:
    engine = ClassificationRuleEngine(
        {
            "rules": [
                {
                    "id": "a",
                    "target": "column",
                    "when": {"name_exact": ["identifier"]},
                    "tag": "PII.NationalIdentifier",
                    "auto_apply": True,
                },
                {
                    "id": "b",
                    "target": "column",
                    "when": {"name_exact": ["identifier"]},
                    "tag": "PII.PaymentCard",
                    "auto_apply": True,
                },
            ]
        }
    )
    event = MetadataEventRequest(
        event_id="evt-2",
        event_type="ENTITY_UPDATED",
        entity_fqn="hive.sales.customers",
        entity_name="customers",
        fields=[MetadataField(name="identifier")],
    )

    result = engine.evaluate(event)

    assert result.outcome == MatchOutcome.AMBIGUOUS
    assert result.trusted_auto_apply is False
