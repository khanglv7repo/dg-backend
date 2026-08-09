from __future__ import annotations

from collections.abc import Generator
from unittest.mock import create_autospec, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db, get_settings
from app.api.routes import data_access_policies
from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.db.base import Base
from app.models.audit import AuditEvent  # noqa: F401
from app.models.data_access_policy import (
    DataAccessPolicyVersion,
    RangerPolicyProjection,
)  # noqa: F401
from app.models.job import GovernanceJob


def policy() -> dict:
    return {
        "subjects": [
            {"type": "USER", "name": "alice"},
            {"type": "GROUP", "name": "pii_readers"},
        ],
        "resource": {"catalog": "dev", "schema": "sales", "table": "customer"},
        "access": {"select": "ALLOW", "insert": "DENY"},
        "masks": {"phone": "MASK"},
        "row_filter": "region = 'VN'",
    }


def test_fastapi_create_preview_activate_status_vertical_slice() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    test_settings = Settings(
        app_env="test",
        ranger_enabled=True,
        ranger_service_name="dev_trino",
        ranger_dry_run=False,
    )

    def db_dependency() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(
        data_access_policies.router,
        prefix="/api/v1/data-access-policies",
    )
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_settings] = lambda: test_settings

    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.side_effect = lambda name: name == "alice"
    ranger.group_exists.side_effect = lambda name: name == "pii_readers"
    ranger.find_by_name.return_value = None

    headers = {
        "X-Actor-Id": "admin",
        "X-Actor-Name": "Admin",
        "X-Actor-Roles": "governance-admin",
    }
    def assert_committed_before_dispatch(*, policy_version_id: str, **_kwargs) -> None:
        with factory() as verification_db:
            durable = verification_db.get(
                DataAccessPolicyVersion,
                UUID(policy_version_id),
            )
            assert durable is not None
            assert durable.status == "ACTIVE"

    with patch.object(
        data_access_policies,
        "build_resource_ranger_client",
        return_value=ranger,
    ), patch.object(data_access_policies.sync_policy_to_ranger, "delay") as delay:
        delay.side_effect = assert_committed_before_dispatch
        client = TestClient(app)
        created = client.post(
            "/api/v1/data-access-policies/sales.customer/versions",
            json={"logical_policy": policy()},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "DRAFT"
        version_id = created.json()["id"]

        preview = client.post(
            "/api/v1/data-access-policies/sales.customer/preview",
            json={"logical_policy": policy()},
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert {item["projection_type"] for item in preview.json()["projections"]} == {
            "ACCESS",
            "MASK",
            "ROW_FILTER",
        }
        assert all(item["action"] == "CREATE" for item in preview.json()["projections"])
        ranger.reconcile_document.assert_not_called()

        activated = client.post(
            "/api/v1/data-access-policies/sales.customer/versions/1/activate",
            headers=headers,
        )
        assert activated.status_code == 202, activated.text
        assert activated.json()["version"]["status"] == "ACTIVE"
        delay.assert_called_once_with(
            policy_version_id=version_id,
            correlation_id=None,
        )
        ranger.reconcile_document.assert_not_called()

        status_response = client.get(
            "/api/v1/data-access-policies/sales.customer/status",
            headers=headers,
        )
        assert status_response.status_code == 200, status_response.text
        body = status_response.json()
        assert body["active_version"]["version"] == 1
        assert {row["sync_status"] for row in body["projections"]} == {"PENDING"}
        assert len(body["projections"]) == 3

        with factory() as verification_db:
            assert verification_db.query(GovernanceJob).count() == 0

    Base.metadata.drop_all(engine)
