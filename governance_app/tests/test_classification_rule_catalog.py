from __future__ import annotations

import json

import pytest

from app.core.errors import (
    ConfigurationError,
    ValidationError,
)
from app.services.classification_rule_catalog import (
    ClassificationRuleCatalogService,
)


DOCUMENT = {
    "version": "test-v1",
    "rules": [
        {
            "id": "email",
            "target": "column",
            "when": {
                "name_exact": ["email"]
            },
            "tag": "PII.Email",
            "confidence": 1.0,
            "auto_apply": True,
        }
    ],
}


def payload(document=DOCUMENT) -> bytes:
    return json.dumps(document).encode("utf-8")


def test_import_json_creates_and_activates_rule_set(
    session,
) -> None:
    record, created, activated = (
        ClassificationRuleCatalogService(
            session
        ).import_json(
            payload(),
            filename="rules.json",
            actor_id="admin",
            actor_name="Admin",
        )
    )

    assert created is True
    assert activated is True
    assert record.status == "ACTIVE"
    assert record.declared_version == "test-v1"

    active = (
        ClassificationRuleCatalogService(
            session
        ).get_active()
    )
    assert active.id == record.id


def test_reimport_same_json_is_idempotent(
    session,
) -> None:
    service = ClassificationRuleCatalogService(
        session
    )

    first, _, _ = service.import_json(
        payload(),
        filename="rules.json",
        actor_id="admin",
        actor_name="Admin",
    )

    second, created, activated = (
        service.import_json(
            payload(),
            filename="rules.json",
            actor_id="admin",
            actor_name="Admin",
        )
    )

    assert created is False
    assert activated is False
    assert second.id == first.id


def test_new_import_becomes_only_active_version(
    session,
) -> None:
    service = ClassificationRuleCatalogService(
        session
    )

    first, _, _ = service.import_json(
        payload(),
        filename="rules-v1.json",
        actor_id="admin",
        actor_name="Admin",
    )

    second_document = {
        **DOCUMENT,
        "version": "test-v2",
        "rules": [
            {
                **DOCUMENT["rules"][0],
                "id": "phone",
                "tag": "PII.Phone",
                "when": {
                    "name_exact": ["phone"]
                },
            }
        ],
    }

    second, _, activated = (
        service.import_json(
            payload(second_document),
            filename="rules-v2.json",
            actor_id="admin",
            actor_name="Admin",
        )
    )

    assert activated is True
    assert second.status == "ACTIVE"

    refreshed_first = service.version_repo.get_by_checksum("default", first.checksum)
    assert refreshed_first.status == "INACTIVE"
    assert service.get_active().id == second.id


def test_import_rejects_non_json_filename(
    session,
) -> None:
    with pytest.raises(
        ValidationError,
        match="JSON files only",
    ):
        ClassificationRuleCatalogService(
            session
        ).import_json(
            payload(),
            filename="rules.yaml",
            actor_id="admin",
            actor_name="Admin",
        )


def test_import_rejects_duplicate_rule_ids(
    session,
) -> None:
    document = {
        "version": "bad",
        "rules": [
            DOCUMENT["rules"][0],
            DOCUMENT["rules"][0],
        ],
    }

    with pytest.raises(
        ValidationError,
        match="duplicate classification rule id",
    ):
        ClassificationRuleCatalogService(
            session
        ).import_json(
            payload(document),
            filename="rules.json",
            actor_id="admin",
            actor_name="Admin",
        )


def test_active_engine_requires_uploaded_rules(
    session,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match="no active classification rule version",
    ):
        ClassificationRuleCatalogService(
            session
        ).active_engine()
