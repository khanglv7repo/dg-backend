from __future__ import annotations

from unittest.mock import create_autospec, patch

from sqlalchemy.orm import sessionmaker

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.services.data_access_policy import DataAccessPolicyService
from app.tasks import policy_sync as policy_task


def policy(select_only: bool = False) -> dict:
    return {
        "subjects": [{"type": "USER", "name": "alice"}],
        "resource": {"catalog": "dev", "schema": "sales", "table": "customer"},
        "access": {"select": "ALLOW"} if select_only else {"select": "ALLOW", "insert": "DENY"},
        "masks": {},
        "row_filter": None,
    }


def test_celery_production_task_rechecks_active_and_fences_stale_v1(session) -> None:
    test_settings = Settings(
        app_env="test",
        ranger_enabled=True,
        ranger_service_name="dev_trino",
        ranger_dry_run=False,
    )
    activation_ranger = create_autospec(RangerClient, instance=True)
    activation_ranger.user_exists.return_value = True
    service = DataAccessPolicyService(
        session,
        test_settings,
        ranger_client=activation_ranger,
    )
    with session.begin():
        v1 = service.create_version(
            policy_key="task-fence",
            logical_policy=policy(),
            actor_id="admin",
            actor_name="Admin",
        )
        v2 = service.create_version(
            policy_key="task-fence",
            logical_policy=policy(select_only=True),
            actor_id="admin",
            actor_name="Admin",
        )
    with session.begin():
        service.activate_version(
            policy_key="task-fence",
            version=v1.version,
            actor_id="admin",
            actor_name="Admin",
        )
    with session.begin():
        service.activate_version(
            policy_key="task-fence",
            version=v2.version,
            actor_id="admin",
            actor_name="Admin",
        )

    worker_ranger = create_autospec(RangerClient, instance=True)
    factory = sessionmaker(
        bind=session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )
    with patch.object(policy_task, "SessionLocal", factory), patch.object(
        policy_task,
        "get_settings",
        return_value=test_settings,
    ), patch.object(
        policy_task,
        "build_resource_ranger_client",
        return_value=worker_ranger,
    ):
        result = policy_task.sync_policy_to_ranger.run(
            policy_version_id=str(v1.id),
        )

    assert result["status"] == "SUPERSEDED"
    assert result["ranger_mutations"] == 0
    worker_ranger.find_by_name.assert_not_called()
    worker_ranger.reconcile_document.assert_not_called()
