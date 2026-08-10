from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.clients.trino_readonly import validate_readonly_sql
from app.core.errors import ValidationError
from app.services.ranger_inspection import RangerInspectionService


def test_inspect_ranger_state_calls_inspection_service() -> None:
    with patch(
        "app.services.ranger_inspection.create_ranger_policy_client"
    ) as mock_policy_factory, patch(
        "app.services.ranger_inspection.create_ranger_tag_store_client"
    ) as mock_tag_factory:
        policy_client = MagicMock()
        policy_client.list_policies.return_value = []
        mock_policy_factory.return_value = policy_client

        tag_client = MagicMock()
        tag_client.list_tag_definitions.return_value = []
        mock_tag_factory.return_value = tag_client

        result = RangerInspectionService().inspect(service_name="dev_trino")

        assert result["service_name"] == "dev_trino"
        assert result["policies"] == []
        assert result["tag_definitions"] == []
        mock_policy_factory.assert_called_once()
        mock_tag_factory.assert_called_once()


def test_query_trino_readonly_rejects_non_read_only() -> None:
    with pytest.raises(ValidationError):
        validate_readonly_sql("DROP TABLE customer")
