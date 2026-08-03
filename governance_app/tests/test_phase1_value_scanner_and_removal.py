from pathlib import Path
from unittest.mock import MagicMock

from app.core.config import Settings
from app.models.data_value_scan import DataValueScanRun
from app.models.enums import JobType
from app.repositories.jobs import JobRepository
from app.rules.value_detectors import DataValueDetectorEngine
from app.services.data_value_scanner import DataValueScannerService
from app.services.policy_catalog import PolicyCatalogService


def test_data_value_detector_engine_matching(tmp_path) -> None:
    yaml_file = tmp_path / "detectors.yaml"
    yaml_file.write_text(
        """version: 1
detectors:
  - id: test-email
    detector_type: email
    tag: PII.Email
    min_match_ratio: 0.5
    min_samples: 2
    regex: "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,}$"
    rationale: Email test
""",
        encoding="utf-8",
    )

    engine = DataValueDetectorEngine.from_path(yaml_file)
    samples = ["alice@example.com", "bob@test.org", "invalid-string"]
    suggestions, metrics = engine.scan_column_samples("columns.contact", samples)

    assert len(suggestions) == 1
    assert suggestions[0].tag == "PII.Email"
    assert metrics["detectors"]["test-email"]["matched"] == 2
    assert metrics["detectors"]["test-email"]["total"] == 3


def test_data_value_scanner_service_never_stores_raw_values(session, tmp_path) -> None:
    yaml_file = tmp_path / "detectors.yaml"
    yaml_file.write_text(
        """version: 1
detectors:
  - id: test-email
    detector_type: email
    tag: PII.Email
    min_match_ratio: 0.5
    min_samples: 2
    regex: "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,}$"
    rationale: Email test
""",
        encoding="utf-8",
    )

    settings = Settings(
        sample_scan_enabled=True,
        data_value_scan_config_path=yaml_file,
    )

    mock_sample_client = MagicMock()
    mock_sample_client.fetch_column_samples.return_value = [
        "secret_user1@sensitive-domain.com",
        "secret_user2@sensitive-domain.com",
    ]

    service = DataValueScannerService(session, settings, sample_client=mock_sample_client)

    fields = [{"name": "contact_email", "data_type": "varchar"}]

    with session.begin():
        res = service.scan(
            entity_type="table",
            entity_fqn="hive.sales.customers",
            fields=fields,
            correlation_id="corr-scan-1",
        )

    assert res["status"] == "COMPLETED"
    assert res["suggestions_created"] == 1

    scan_run = session.query(DataValueScanRun).first()
    assert scan_run is not None
    assert scan_run.total_samples == 2
    assert scan_run.matched_samples == 2

    metrics_str = str(scan_run.metrics)
    assert "secret_user1" not in metrics_str
    assert "sensitive-domain" not in metrics_str

    claimed = JobRepository(session).claim_batch(worker_id="test", limit=10)
    assert len(claimed) == 1
    assert claimed[0].job_type == JobType.CREATE_OM_SUGGESTIONS.value


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
