from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db.base import Base
from app.repositories.classification_rule_sets import (
    ClassificationRuleSetRepository,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(engine)
    with factory() as db:
        yield db
    Base.metadata.drop_all(engine)


@pytest.fixture()
def classification_rule_document() -> dict:
    return {
        "version": "test-rules-v1",
        "rules": [
            {
                "id": "pii-email-exact",
                "target": "column",
                "when": {
                    "name_exact": [
                        "email",
                        "email_address",
                        "e_mail",
                    ]
                },
                "tag": "PII.Email",
                "confidence": 1.0,
                "auto_apply": True,
                "rationale": ("Exact governed column name for an email address."),
            },
            {
                "id": "pii-email-pattern",
                "target": "column",
                "when": {"name_regex": ("^(customer_|work_|personal_)?e-?mail_address$")},
                "tag": "PII.Email",
                "confidence": 0.94,
                "auto_apply": False,
                "rationale": ("Column name strongly resembles an email address."),
            },
            {
                "id": "pii-phone",
                "target": "column",
                "when": {"name_regex": ("(^|_)(phone|mobile|telephone)(_number)?$")},
                "tag": "PII.Phone",
                "confidence": 0.93,
                "auto_apply": False,
            },
        ],
    }


@pytest.fixture()
def active_classification_rules(
    session,
    classification_rule_document,
):
    canonical = json.dumps(
        classification_rule_document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    with session.begin():
        repository = ClassificationRuleSetRepository(session)
        record = repository.create(
            name="default",
            declared_version=str(classification_rule_document["version"]),
            document=classification_rule_document,
            document_sha256=sha256,
            created_by="test-suite",
            created_by_name="Test Suite",
        )
        record, _ = repository.activate(record.id)

    return record
