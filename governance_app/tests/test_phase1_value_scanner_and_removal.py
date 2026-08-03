from pathlib import Path
from unittest.mock import MagicMock

from app.core.config import Settings
from app.models.enums import JobType
from app.repositories.jobs import JobRepository
from app.services.policy_catalog import PolicyCatalogService






def test_policy_removal_is_explicit_db_disable(session) -> None:
    """New architecture: tag removal never generates/removes Ranger policies.

    Policy lifecycle is explicit desired state in PostgreSQL. A delete command is
    a soft-disable that the Ranger catalog reconciler later applies.
    """

    settings = Settings(
        _env_file=None,
        ranger_service_name="dev_trino",
        ranger_tag_service_name="dev_tag",
    )
    document = {
        "isEnabled": True,
        "service": "dev_tag",
        "serviceType": "tag",
        "name": "dg-tag-pii-email",
        "description": "Allow PII readers to select PII.Email",
        "resources": {
            "tag": {
                "values": ["PII.Email"],
                "isExcludes": False,
                "isRecursive": False,
            }
        },
        "policyItems": [
            {
                "accesses": [
                    {"type": "trino:select", "isAllowed": True}
                ],
                "groups": ["pii_readers"],
                "delegateAdmin": False,
            }
        ],
    }

    service = PolicyCatalogService(session, settings)
    with session.begin():
        policy, created, changed = service.import_document(
            document,
            actor_id="test",
            actor_name="Test",
        )

    assert created is True
    assert changed is True
    assert policy.enabled is True
    assert policy.revision == 1

    with session.begin():
        disabled = service.disable(
            policy.id,
            actor_id="test",
            actor_name="Test",
            correlation_id="corr-removal-1",
        )

    assert disabled.enabled is False
    assert disabled.document["isEnabled"] is False
    assert disabled.revision == 2
