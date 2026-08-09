from __future__ import annotations

import tempfile
from collections.abc import Generator
from unittest.mock import create_autospec, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_db, get_settings
from app.api.router import api_router
from app.api.routes import data_access_policies
from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.core.security import Actor
from app.db.base import Base
from app.models.audit import AuditEvent  # noqa: F401
from app.models.data_access_policy import (
    DataAccessPolicyVersion,
    RangerPolicyProjection,
)  # noqa: F401
from app.models.job import GovernanceJob


TEST_SETTINGS = Settings(
    app_env="test",
    ranger_enabled=True,
    ranger_service_name="dev_trino",
    ranger_dry_run=False,
)
TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


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


def select_policy(*, user: str = "alice") -> dict:
    return {
        "subjects": [{"type": "USER", "name": user}],
        "resource": {"catalog": "dev", "schema": "sales", "table": "customer"},
        "access": {"select": "ALLOW"},
        "masks": {},
        "row_filter": None,
    }


def build_test_db():
    tmpdir = tempfile.TemporaryDirectory()
    db_path = f"{tmpdir.name}/policy-api-test.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    TEMP_DIRS.append(tmpdir)

    return factory, engine


def admin_actor() -> Actor:
    return Actor(
        subject="admin",
        display_name="Admin",
        roles=frozenset({"governance-admin"}),
    )


def admin_headers() -> dict[str, str]:
    return {
        "X-Actor-Id": "admin",
        "X-Actor-Name": "Admin",
        "X-Actor-Roles": "governance-admin",
    }


def test_real_fastapi_testclient_create_preview_activate_status_vertical_slice() -> None:
    factory, engine = build_test_db()

    def db_dependency() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(api_router, prefix=TEST_SETTINGS.api_prefix)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS

    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.side_effect = lambda name: name == "alice"
    ranger.group_exists.side_effect = lambda name: name == "pii_readers"
    ranger.find_by_name.return_value = None

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
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/data-access-policies/http.sales.customer/versions",
                json={"logical_policy": policy()},
                headers=admin_headers(),
            )
            assert created.status_code == 201, created.text
            created_body = created.json()
            assert created_body["status"] == "DRAFT"
            version_id = created_body["id"]

            preview = client.post(
                "/api/v1/data-access-policies/http.sales.customer/preview",
                json={"logical_policy": policy()},
                headers=admin_headers(),
            )
            assert preview.status_code == 200, preview.text
            projection_types = {
                item["projection_type"] for item in preview.json()["projections"]
            }
            assert projection_types == {"ACCESS", "MASK", "ROW_FILTER"}
            assert all(
                item["action"] == "CREATE"
                for item in preview.json()["projections"]
            )
            ranger.reconcile_document.assert_not_called()

            activated = client.post(
                "/api/v1/data-access-policies/http.sales.customer/versions/1/activate",
                headers=admin_headers(),
            )
            assert activated.status_code == 202, activated.text
            assert activated.json()["version"]["status"] == "ACTIVE"
            delay.assert_called_once_with(
                policy_version_id=version_id,
                correlation_id=None,
            )
            ranger.reconcile_document.assert_not_called()

            status_response = client.get(
                "/api/v1/data-access-policies/http.sales.customer/status",
                headers=admin_headers(),
            )
            assert status_response.status_code == 200, status_response.text
            body = status_response.json()
            assert body["active_version"]["version"] == 1
            assert body["active_version"]["status"] == "ACTIVE"
            assert {row["sync_status"] for row in body["projections"]} == {"PENDING"}
            assert len(body["projections"]) == 3

    with factory() as verification_db:
        assert verification_db.query(GovernanceJob).count() == 0

    Base.metadata.drop_all(engine)


def test_fastapi_create_preview_activate_status_vertical_slice() -> None:
    factory, engine = build_test_db()

    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.side_effect = lambda name: name == "alice"
    ranger.group_exists.side_effect = lambda name: name == "pii_readers"
    ranger.find_by_name.return_value = None

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
        with factory() as db:
            created = data_access_policies.create_policy_version(
                "sales.customer",
                data_access_policies.CreatePolicyVersionRequest(
                    logical_policy=policy()
                ),
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        assert created.status == "DRAFT"
        version_id = str(created.id)

        with factory() as db:
            preview = data_access_policies.preview_policy(
                "sales.customer",
                data_access_policies.PreviewPolicyRequest(
                    logical_policy=policy()
                ),
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        assert {item.projection_type for item in preview.projections} == {
            "ACCESS",
            "MASK",
            "ROW_FILTER",
        }
        assert all(item.action == "CREATE" for item in preview.projections)
        ranger.reconcile_document.assert_not_called()

        with factory() as db:
            activated = data_access_policies.activate_policy_version(
                "sales.customer",
                1,
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        assert activated.version.status == "ACTIVE"
        delay.assert_called_once_with(
            policy_version_id=version_id,
            correlation_id=None,
        )
        ranger.reconcile_document.assert_not_called()

        with factory() as db:
            status_response = data_access_policies.get_policy_status(
                "sales.customer",
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        assert status_response.active_version.version == 1
        assert {row.sync_status for row in status_response.projections} == {"PENDING"}
        assert len(status_response.projections) == 3

        with factory() as verification_db:
            assert verification_db.query(GovernanceJob).count() == 0

    Base.metadata.drop_all(engine)


def test_activation_broker_failure_recovery_republishes_same_active_version() -> None:
    factory, engine = build_test_db()
    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.return_value = True
    ranger.group_exists.return_value = True
    published: list[str] = []

    def publish_or_fail(*, policy_version_id: str, **_kwargs) -> None:
        published.append(policy_version_id)
        if len(published) == 1:
            raise RuntimeError("broker unavailable")

    with patch.object(
        data_access_policies,
        "build_resource_ranger_client",
        return_value=ranger,
    ), patch.object(data_access_policies.sync_policy_to_ranger, "delay") as delay:
        delay.side_effect = publish_or_fail
        with factory() as db:
            created = data_access_policies.create_policy_version(
                "retry.activation",
                data_access_policies.CreatePolicyVersionRequest(
                    logical_policy=select_policy()
                ),
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        version_id = str(created.id)

        with pytest.raises(ExternalSystemError) as excinfo:
            with factory() as db:
                data_access_policies.activate_policy_version(
                    "retry.activation",
                    1,
                    db,
                    TEST_SETTINGS,
                    admin_actor(),
                )
        assert excinfo.value.retryable is True
        assert excinfo.value.details["policy_version_id"] == version_id

        with factory() as db:
            durable = db.get(DataAccessPolicyVersion, UUID(version_id))
            assert durable is not None
            assert durable.status == "ACTIVE"
            first_activated_at = durable.activated_at
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "DATA_ACCESS_POLICY_VERSION_ACTIVATED")
                .count()
                == 1
            )

        with factory() as db:
            recovered = data_access_policies.activate_policy_version(
                "retry.activation",
                1,
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        assert recovered.version.id == UUID(version_id)

        with factory() as db:
            durable = db.get(DataAccessPolicyVersion, UUID(version_id))
            assert durable is not None
            assert durable.status == "ACTIVE"
            assert durable.activated_at == first_activated_at
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "DATA_ACCESS_POLICY_VERSION_ACTIVATED")
                .count()
                == 1
            )
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "DATA_ACCESS_POLICY_ACTIVATION_NO_CHANGE")
                .count()
                == 1
            )
        assert published == [version_id, version_id]

    Base.metadata.drop_all(engine)


def test_rollback_broker_failure_recovery_targets_explicit_version_without_walkback() -> None:
    factory, engine = build_test_db()
    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.return_value = True
    ranger.group_exists.return_value = True
    published: list[str] = []

    def publish_or_fail(*, policy_version_id: str, **_kwargs) -> None:
        published.append(policy_version_id)
        if len(published) == 4:
            raise RuntimeError("broker unavailable after rollback commit")

    with patch.object(
        data_access_policies,
        "build_resource_ranger_client",
        return_value=ranger,
    ), patch.object(data_access_policies.sync_policy_to_ranger, "delay") as delay:
        delay.side_effect = publish_or_fail
        version_ids: dict[int, str] = {}
        for version, user in [(1, "alice"), (2, "bob"), (3, "carol")]:
            with factory() as db:
                created = data_access_policies.create_policy_version(
                    "retry.rollback",
                    data_access_policies.CreatePolicyVersionRequest(
                        logical_policy=select_policy(user=user)
                    ),
                    db,
                    TEST_SETTINGS,
                    admin_actor(),
                )
            version_ids[version] = str(created.id)
            with factory() as db:
                activated = data_access_policies.activate_policy_version(
                    "retry.rollback",
                    version,
                    db,
                    TEST_SETTINGS,
                    admin_actor(),
                )
            assert activated.version.version == version

        with pytest.raises(ExternalSystemError) as excinfo:
            with factory() as db:
                data_access_policies.rollback_policy(
                    "retry.rollback",
                    data_access_policies.RollbackPolicyRequest(target_version=2),
                    db,
                    TEST_SETTINGS,
                    admin_actor(),
                )
        assert excinfo.value.retryable is True

        with factory() as db:
            states = {
                row.version: row.status
                for row in db.query(DataAccessPolicyVersion)
                .filter(DataAccessPolicyVersion.policy_key == "retry.rollback")
                .all()
            }
            assert states == {1: "INACTIVE", 2: "ACTIVE", 3: "INACTIVE"}
            assert (
                db.query(DataAccessPolicyVersion)
                .filter(
                    DataAccessPolicyVersion.policy_key == "retry.rollback",
                    DataAccessPolicyVersion.status == "ACTIVE",
                )
                .count()
                == 1
            )
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "DATA_ACCESS_POLICY_ROLLED_BACK")
                .count()
                == 1
            )

        with factory() as db:
            recovered = data_access_policies.rollback_policy(
                "retry.rollback",
                data_access_policies.RollbackPolicyRequest(target_version=2),
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
        assert recovered.version.id == UUID(version_ids[2])

        with factory() as db:
            states = {
                row.version: row.status
                for row in db.query(DataAccessPolicyVersion)
                .filter(DataAccessPolicyVersion.policy_key == "retry.rollback")
                .all()
            }
            assert states == {1: "INACTIVE", 2: "ACTIVE", 3: "INACTIVE"}
            assert (
                db.query(DataAccessPolicyVersion)
                .filter(
                    DataAccessPolicyVersion.policy_key == "retry.rollback",
                    DataAccessPolicyVersion.status == "ACTIVE",
                )
                .count()
                == 1
            )
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "DATA_ACCESS_POLICY_ROLLED_BACK")
                .count()
                == 1
            )
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "DATA_ACCESS_POLICY_ACTIVATION_NO_CHANGE")
                .count()
                == 1
            )

        assert published == [
            version_ids[1],
            version_ids[2],
            version_ids[3],
            version_ids[2],
            version_ids[2],
        ]
        delay.assert_called_with(
            policy_version_id=version_ids[2],
            correlation_id=None,
        )

        with pytest.raises(PydanticValidationError):
            data_access_policies.RollbackPolicyRequest()

    Base.metadata.drop_all(engine)


def test_ranger_subject_validation_occurs_outside_authoritative_write_tx() -> None:
    factory, engine = build_test_db()
    holder = {}

    ranger = create_autospec(RangerClient, instance=True)

    def user_exists(_name: str) -> bool:
        assert holder["db"].in_transaction() is False
        return True

    ranger.user_exists.side_effect = user_exists
    ranger.group_exists.return_value = True
    with patch.object(
        data_access_policies,
        "build_resource_ranger_client",
        return_value=ranger,
    ), patch.object(data_access_policies.sync_policy_to_ranger, "delay"):
        with factory() as db:
            holder["db"] = db
            created = data_access_policies.create_policy_version(
                "tx.boundary",
                data_access_policies.CreatePolicyVersionRequest(
                    logical_policy=select_policy()
                ),
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
            assert created.status == "DRAFT"
            activated = data_access_policies.activate_policy_version(
                "tx.boundary",
                1,
                db,
                TEST_SETTINGS,
                admin_actor(),
            )
            assert activated.version.status == "ACTIVE"

    Base.metadata.drop_all(engine)
