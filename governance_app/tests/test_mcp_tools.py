from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from app.core.errors import ValidationError
from app.mcp.backend_mcp_server import inspect_ranger_state, query_trino_readonly


def test_inspect_ranger_state_calls_inspection_service() -> None:
    with patch("app.mcp.backend_mcp_server.RangerInspectionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service.inspect.return_value = {
            "service_name": "dev_trino",
            "tag_service_name": "dev_tag",
            "policies_count": 0,
            "policies": [],
            "tag_definitions": [],
        }
        mock_service_cls.return_value = mock_service

        result = inspect_ranger_state(service_name="dev_trino")
        assert result["service_name"] == "dev_trino"
        mock_service.inspect.assert_called_once_with(service_name="dev_trino")


def test_query_trino_readonly_rejects_non_read_only() -> None:
    with pytest.raises(ValidationError):
        query_trino_readonly(query="DROP TABLE customer", username="alice")
