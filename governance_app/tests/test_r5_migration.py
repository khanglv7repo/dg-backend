from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(db_path: Path, *args: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite+pysqlite:///{db_path}",
        }
    )
    subprocess.run(
        ["alembic", "-c", "alembic.ini", *args],
        cwd=APP_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _assert_mapping_schema(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "service_mapping" in tables
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('service_mapping')")
        }
        assert {
            "id",
            "om_service_name",
            "trino_catalog",
            "ranger_service_name",
            "ranger_tag_service_name",
            "environment",
            "enabled",
            "created_at",
            "updated_at",
            "updated_by",
        } <= columns
        unique_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='sqlite_autoindex_service_mapping_1'"
        ).fetchone()
        # SQLite may keep auto-index SQL as NULL; exercise the invariant instead.
        connection.execute(
            "INSERT INTO service_mapping "
            "(id, om_service_name, trino_catalog, ranger_service_name, environment, "
            "enabled, created_at, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "00000000000000000000000000000001",
                "financial_postgres",
                "financial",
                "dev_trino",
                "local",
                1,
                "2026-08-09",
                "2026-08-09",
                "test",
            ),
        )
        try:
            connection.execute(
                "INSERT INTO service_mapping "
                "(id, om_service_name, trino_catalog, ranger_service_name, environment, "
                "enabled, created_at, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "00000000000000000000000000000002",
                    "financial_postgres",
                    "other",
                    "dev_trino",
                    "local",
                    1,
                    "2026-08-09",
                    "2026-08-09",
                    "test",
                ),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("service_mapping exact identity must be unique")
        connection.rollback()
    finally:
        connection.close()


def test_r5_clean_database_migrates_to_head(tmp_path: Path) -> None:
    db_path = tmp_path / "r5-clean.db"
    _run_alembic(db_path, "upgrade", "head")
    _assert_mapping_schema(db_path)


def test_existing_0011_database_migrates_to_r5_head(tmp_path: Path) -> None:
    db_path = tmp_path / "from-0011.db"
    _run_alembic(db_path, "upgrade", "0011_r4_data_access_policy")
    _run_alembic(db_path, "upgrade", "head")
    _assert_mapping_schema(db_path)
