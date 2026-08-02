from unittest.mock import MagicMock

import pytest

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.models.data_value_scan import DataValueScanRun
from app.models.enums import JobType, ReconciliationAction
from app.repositories.jobs import JobRepository
from app.rules.value_detectors import DataValueDetectorEngine
from app.services.data_value_scanner import DataValueScannerService
from app.services.policy_sync import PolicySyncService


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

    # Verify scan run in database
    scan_run = session.query(DataValueScanRun).first()
    assert scan_run is not None
    assert scan_run.total_samples == 2
    assert scan_run.matched_samples == 2

    # Assert raw values ("secret_user1@sensitive-domain.com") are NOT stored in metrics
    metrics_str = str(scan_run.metrics)
    assert "secret_user1" not in metrics_str
    assert "sensitive-domain" not in metrics_str

    # Assert enqueued job is CREATE_OM_SUGGESTIONS (never auto-apply)
    claimed = JobRepository(session).claim_batch(worker_id="test", limit=10)
    assert len(claimed) == 1
    assert claimed[0].job_type == JobType.CREATE_OM_SUGGESTIONS.value


def test_ranger_tag_removal_reconciliation_disable(session) -> None:
    settings = Settings(
        ranger_enabled=True,
        ranger_dry_run=False,
        ranger_allow_policy_delete=False,
    )

    mock_ranger = MagicMock(spec=RangerClient)
    mock_ranger.dry_run = False

    # Simulate existing policy for removed tag PII.Email
    existing_policy = {
        "id": 101,
        "name": "dg-pii-email-hive.sales.customers-email",
        "description": "PII Email | managed-by=dg-backend;",
        "isEnabled": True,
    }
    mock_ranger.find_by_name.return_value = existing_policy
    mock_ranger.reconcile_removal.return_value = {
        "action": ReconciliationAction.DISABLE.value,
        "desired_hash": "disabled-hash",
        "observed_hash": "old-hash",
        "policy_id": "101",
        "document": {**existing_policy, "isEnabled": False},
    }

    service = PolicySyncService(session, settings, mock_ranger)

    # Sync with EMPTY tags (tag was removed)
    with session.begin():
        res = service.sync(
            entity_fqn="hive.sales.customers",
            tags=[],
            field_paths={},
            classification_run_id=None,
            correlation_id="corr-removal-1",
        )

    assert len(res["reconciliations"]) >= 1
    actions = [r["action"] for r in res["reconciliations"]]
    assert ReconciliationAction.DISABLE.value in actions
