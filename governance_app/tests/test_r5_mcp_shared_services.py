from __future__ import annotations

import tempfile
from collections.abc import Generator
from unittest.mock import MagicMock, create_autospec, patch
from uuid import UUID

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401
from app.api.dependencies import get_db, get_settings
from app.api.router import api_router
from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.db.base import Base
from app.mcp import backend_mcp_server
from app.models.data_access_policy import DataAccessPolicyVersion

TEST_SETTINGS = Settings(
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


def build_test_db():
    tmpdir = tempfile.TemporaryDirectory()
    db_path = f"{tmpdir.name}/r5-shared.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return tmpdir, factory, engine


def build_app(factory) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix=TEST_SETTINGS.api_prefix)

    def db_dependency() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return app


def admin_headers() -> dict[str, str]:
    return {
        "X-Actor-Id": "admin",
        "X-Actor-Name": "Admin",
        "X-Actor-Roles": "governance-admin",
    }


def test_mcp_create_is_visible_via_rest_and_rest_create_is_visible_via_mcp() -> None:
    tmpdir, factory, engine = build_test_db()
    app = build_app(factory)

    async def run() -> None:
        with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
            backend_mcp_server,
            "get_settings",
            return_value=TEST_SETTINGS,
        ):
            async with Client(backend_mcp_server.mcp) as mcp_client:
                mcp_created = await mcp_client.call_tool(
                    "create_policy_version",
                    {
                        "policy_key": "mcp-created",
                        "logical_policy": policy(),
                        "reason": "proposal only",
                    },
                )
                assert mcp_created.data["status"] == "DRAFT"
                assert mcp_created.data["authority_changed"] is False
                assert mcp_created.data["dispatched"] is False

                with TestClient(app) as rest:
                    from_mcp = rest.get(
                        "/api/v1/data-access-policies/mcp-created/versions/1",
                        headers=admin_headers(),
                    )
                    assert from_mcp.status_code == 200
                    assert from_mcp.json()["status"] == "DRAFT"

                    rest_created = rest.post(
                        "/api/v1/data-access-policies/rest-created/versions",
                        json={"logical_policy": policy()},
                        headers=admin_headers(),
                    )
                    assert rest_created.status_code == 201

                from_rest = await mcp_client.call_tool(
                    "get_policy",
                    {"policy_key": "rest-created", "version": 1},
                )
                assert from_rest.data["status"] == "DRAFT"
                assert from_rest.data["checksum"] == rest_created.json()["checksum"]

                versions = await mcp_client.call_tool(
                    "list_policy_versions",
                    {"policy_key": "rest-created"},
                )
                version_rows = versions.structured_content["result"]
                assert [item["version"] for item in version_rows] == [1]

    try:
        anyio.run(run)
    finally:
        Base.metadata.drop_all(engine)
        tmpdir.cleanup()


def test_mcp_confirmation_activation_commit_before_dispatch_and_request_sync() -> None:
    tmpdir, factory, engine = build_test_db()
    app = build_app(factory)
    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.side_effect = lambda name: name == "alice"
    ranger.group_exists.return_value = False
    ranger.find_by_name.return_value = None

    task = MagicMock()
    task.id = "task-r5-activate"

    def committed_active_before_dispatch(*, policy_version_id: str, **_kwargs):
        with factory() as verify:
            row = verify.get(DataAccessPolicyVersion, UUID(policy_version_id))
            assert row is not None
            assert row.status == "ACTIVE"
        return task

    async def run() -> None:
        with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
            backend_mcp_server,
            "get_settings",
            return_value=TEST_SETTINGS,
        ), patch.object(
            backend_mcp_server,
            "build_resource_ranger_client",
            return_value=ranger,
        ), patch(
            "app.services.policy_lifecycle.sync_policy_to_ranger.delay",
            side_effect=committed_active_before_dispatch,
        ) as delay:
            async with Client(backend_mcp_server.mcp) as mcp_client:
                created = await mcp_client.call_tool(
                    "create_policy_version",
                    {"policy_key": "activation", "logical_policy": policy()},
                )
                version_id = created.data["id"]

                try:
                    await mcp_client.call_tool(
                        "activate_policy_version",
                        {
                            "policy_key": "activation",
                            "version": 1,
                            "confirmed": False,
                        },
                    )
                except ToolError as exc:
                    assert "CONFIRMATION_REQUIRED" in str(exc)
                else:
                    raise AssertionError("confirmed=false must fail")

                with factory() as verify:
                    row = verify.get(DataAccessPolicyVersion, UUID(version_id))
                    assert row is not None and row.status == "DRAFT"

                activated = await mcp_client.call_tool(
                    "activate_policy_version",
                    {
                        "policy_key": "activation",
                        "version": 1,
                        "confirmed": True,
                    },
                )
                assert activated.data["status"] == "ACTIVE"
                assert activated.data["authority_changed"] is True
                assert activated.data["dispatched"] is True

                with TestClient(app) as rest:
                    status = rest.get(
                        "/api/v1/data-access-policies/activation/status",
                        headers=admin_headers(),
                    )
                    assert status.status_code == 200
                    assert status.json()["active_version"]["version"] == 1

                sync_status = await mcp_client.call_tool(
                    "get_ranger_sync_status",
                    {"policy_key": "activation"},
                )
                assert {
                    item["sync_status"] for item in sync_status.data["projections"]
                } == {"PENDING"}

                retry = await mcp_client.call_tool(
                    "activate_policy_version",
                    {
                        "policy_key": "activation",
                        "version": 1,
                        "confirmed": True,
                    },
                )
                assert retry.data["authority_changed"] is False

                sync = await mcp_client.call_tool(
                    "request_ranger_sync",
                    {"policy_key": "activation"},
                )
                assert sync.data["policy_version_id"] == version_id
                assert sync.data["authority_changed"] is False
                assert delay.call_count == 3

    try:
        anyio.run(run)
    finally:
        Base.metadata.drop_all(engine)
        tmpdir.cleanup()
