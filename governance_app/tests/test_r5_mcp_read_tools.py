from __future__ import annotations

import tempfile
from unittest.mock import create_autospec, patch

import anyio
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.db.base import Base
from app.mcp import backend_mcp_server
from app.repositories.classification_execution import ClassificationExecutionRepository

SETTINGS = Settings(
    app_env="test",
    ranger_enabled=True,
    ranger_service_name="dev_trino",
    ranger_dry_run=False,
    mcp_enabled=True,
    mcp_actor_id="r5-mcp-test",
    mcp_actor_name="R5 MCP Test",
)


def policy() -> dict:
    return {
        "subjects": [{"type": "USER", "name": "alice"}],
        "resource": {"catalog": "dev", "schema": "sales", "table": "customer"},
        "access": {"select": "ALLOW"},
        "masks": {"phone": "MASK"},
        "row_filter": "region = 'VN'",
    }


def test_mcp_read_diagnostics_mapping_and_preview_use_bounded_services() -> None:
    tmpdir = tempfile.TemporaryDirectory()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmpdir.name}/r5-read-tools.db",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with factory() as seed:
        with seed.begin():
            execution = ClassificationExecutionRepository(seed).create(
                event_id="evt-r5-mcp-read",
                entity_type="table",
                entity_fqn="dev.sales.customer",
                status="WAITING_AI",
                outcome="NO_MATCH",
            )
        execution_id = str(execution.id)

    ranger = create_autospec(RangerClient, instance=True)
    ranger.service_name = "dev_trino"
    ranger.user_exists.side_effect = lambda name: name == "alice"
    ranger.group_exists.return_value = False
    ranger.find_user.return_value = {"id": 1, "name": "alice"}
    ranger.find_group.return_value = None
    ranger.find_by_name.return_value = None
    ranger.owns_policy.return_value = True
    ranger.health.return_value = {"name": "dev_trino"}

    async def run() -> None:
        with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
            backend_mcp_server,
            "get_settings",
            return_value=SETTINGS,
        ), patch.object(
            backend_mcp_server,
            "build_resource_ranger_client",
            return_value=ranger,
        ), patch.object(
            backend_mcp_server.TrinoReadonlyService,
            "query",
            return_value={
                "columns": ["n"],
                "rows": [[10]],
                "row_count_returned": 1,
                "truncated": False,
                "query_id": "query-r5",
            },
        ), patch(
            "app.services.policy_lifecycle.sync_policy_to_ranger.delay"
        ) as sync_delay:
            async with Client(backend_mcp_server.mcp) as client:
                preview = await client.call_tool(
                    "preview_policy_change",
                    {"policy_key": "preview-only", "logical_policy": policy()},
                )
                assert {
                    item["projection_type"] for item in preview.data["projections"]
                } == {"ACCESS", "MASK", "ROW_FILTER"}
                assert all(
                    item["action"] == "CREATE" for item in preview.data["projections"]
                )
                ranger.reconcile_document.assert_not_called()
                sync_delay.assert_not_called()

                conflict = await client.call_tool(
                    "check_policy_conflict",
                    {"policy_key": "preview-only", "logical_policy": policy()},
                )
                assert conflict.data["conflict"] is False

                try:
                    await client.call_tool(
                        "update_service_mapping",
                        {
                            "om_service_name": "postgres",
                            "trino_catalog": "dev",
                            "ranger_service_name": "dev_trino",
                            "environment": "local",
                            "confirmed": False,
                        },
                    )
                except ToolError as exc:
                    assert "CONFIRMATION_REQUIRED" in str(exc)
                else:
                    raise AssertionError("mapping update must require confirmation")

                updated = await client.call_tool(
                    "update_service_mapping",
                    {
                        "om_service_name": "postgres",
                        "trino_catalog": "dev",
                        "ranger_service_name": "dev_trino",
                        "ranger_tag_service_name": "dev_tag",
                        "environment": "local",
                        "confirmed": True,
                    },
                )
                assert updated.data["status"] == "RESOLVED"
                assert updated.data["ranger_mutation"] is False

                resolved = await client.call_tool(
                    "resolve_resource_mapping",
                    {"om_service_name": "postgres", "environment": "local"},
                )
                assert resolved.data["trino_catalog"] == "dev"

                try:
                    await client.call_tool(
                        "resolve_resource_mapping",
                        {"om_service_name": "post", "environment": "local"},
                    )
                except ToolError as exc:
                    assert "UNRESOLVED" in str(exc)
                else:
                    raise AssertionError("fuzzy/unresolved mapping must not be guessed")

                workflow = await client.call_tool(
                    "get_workflow_status",
                    {"execution_id": execution_id},
                )
                assert workflow.data["status"] == "WAITING_AI"

                audit = await client.call_tool(
                    "get_audit_summary",
                    {"object_type": "service-mapping", "limit": 1000},
                )
                assert audit.data["limit"] == 100
                assert audit.data["returned"] == 1

                health = await client.call_tool(
                    "inspect_ranger_state",
                    {"kind": "health"},
                )
                assert health.data["service_name"] == "dev_trino"
                user = await client.call_tool(
                    "inspect_ranger_state",
                    {"kind": "user", "name": "alice"},
                )
                assert user.data["exists"] is True
                ranger.reconcile_document.assert_not_called()

                trino = await client.call_tool(
                    "query_trino_readonly",
                    {"sql": "SELECT count(*) FROM dev.sales.customer"},
                )
                assert trino.data["rows"] == [[10]]

    try:
        anyio.run(run)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        tmpdir.cleanup()
