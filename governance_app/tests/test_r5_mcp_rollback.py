from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, create_autospec, patch

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

SETTINGS = Settings(
    app_env="test",
    ranger_enabled=True,
    ranger_service_name="dev_trino",
    ranger_dry_run=False,
    mcp_enabled=True,
)


def policy(table: str) -> dict:
    return {
        "subjects": [{"type": "USER", "name": "alice"}],
        "resource": {"catalog": "dev", "schema": "sales", "table": table},
        "access": {"select": "ALLOW"},
        "masks": {},
        "row_filter": None,
    }


def test_mcp_rollback_requires_explicit_confirmation_and_exact_target() -> None:
    tmpdir = tempfile.TemporaryDirectory()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmpdir.name}/r5-rollback.db",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.return_value = True
    ranger.group_exists.return_value = False
    ranger.find_by_name.return_value = None
    task = MagicMock()
    task.id = "task-r5"

    async def run() -> None:
        with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
            backend_mcp_server,
            "get_settings",
            return_value=SETTINGS,
        ), patch.object(
            backend_mcp_server,
            "build_resource_ranger_client",
            return_value=ranger,
        ), patch(
            "app.services.policy_lifecycle.sync_policy_to_ranger.delay",
            return_value=task,
        ) as delay:
            async with Client(backend_mcp_server.mcp) as client:
                await client.call_tool(
                    "create_policy_version",
                    {"policy_key": "rollback", "logical_policy": policy("customer")},
                )
                await client.call_tool(
                    "create_policy_version",
                    {"policy_key": "rollback", "logical_policy": policy("customer_v2")},
                )
                await client.call_tool(
                    "activate_policy_version",
                    {"policy_key": "rollback", "version": 2, "confirmed": True},
                )

                try:
                    await client.call_tool(
                        "rollback_policy",
                        {
                            "policy_key": "rollback",
                            "target_version": 1,
                            "confirmed": False,
                        },
                    )
                except ToolError as exc:
                    assert "CONFIRMATION_REQUIRED" in str(exc)
                else:
                    raise AssertionError("rollback confirmed=false must fail")

                rolled = await client.call_tool(
                    "rollback_policy",
                    {
                        "policy_key": "rollback",
                        "target_version": 1,
                        "confirmed": True,
                    },
                )
                assert rolled.data["version"] == 1
                assert rolled.data["status"] == "ACTIVE"
                assert rolled.data["authority_changed"] is True

                same_target_retry = await client.call_tool(
                    "rollback_policy",
                    {
                        "policy_key": "rollback",
                        "target_version": 1,
                        "confirmed": True,
                    },
                )
                assert same_target_retry.data["version"] == 1
                assert same_target_retry.data["authority_changed"] is False
                assert delay.call_count == 3

    try:
        anyio.run(run)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        tmpdir.cleanup()
