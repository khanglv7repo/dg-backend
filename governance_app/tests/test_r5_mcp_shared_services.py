from __future__ import annotations

import tempfile
from collections.abc import Generator
from unittest.mock import patch

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401
from app.api.dependencies import get_db, get_settings
from app.api.router import api_router
from app.core.config import Settings
from app.db.base import Base
from app.mcp import backend_mcp_server

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


