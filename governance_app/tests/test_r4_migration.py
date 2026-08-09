from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest


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


def _assert_r4_schema(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "data_access_policy_version" in tables
        assert "ranger_policy_projection" in tables

        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('data_access_policy_version')"
            )
        }
        assert {
            "id",
            "policy_key",
            "version",
            "status",
            "logical_policy",
            "checksum",
            "created_by",
            "created_at",
            "activated_at",
        } <= columns

        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='uq_data_access_policy_version_one_active'"
        ).fetchone()
        assert index_sql is not None
        assert "UNIQUE INDEX" in index_sql[0].upper()
        assert "WHERE status = 'ACTIVE'" in index_sql[0]

        # Exercise both uniqueness invariants against the migrated database,
        # not merely SQLAlchemy model metadata.
        connection.execute(
            "INSERT INTO data_access_policy_version "
            "(id, policy_key, version, status, logical_policy, checksum, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "00000000000000000000000000000001",
                "p",
                1,
                "ACTIVE",
                "{}",
                "a" * 64,
                "t",
                "2026-08-09",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO data_access_policy_version "
                "(id, policy_key, version, status, logical_policy, checksum, "
                "created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "00000000000000000000000000000002",
                    "p",
                    2,
                    "ACTIVE",
                    "{}",
                    "b" * 64,
                    "t",
                    "2026-08-09",
                ),
            )
        connection.rollback()
    finally:
        connection.close()


def test_clean_database_migrates_to_head(tmp_path: Path) -> None:
    db_path = tmp_path / "clean.db"
    _run_alembic(db_path, "upgrade", "head")
    _assert_r4_schema(db_path)


def test_existing_0010_database_migrates_to_r4_head(tmp_path: Path) -> None:
    db_path = tmp_path / "from-0010.db"
    _run_alembic(db_path, "upgrade", "0010_r3_final_correctness")
    _run_alembic(db_path, "upgrade", "head")
    _assert_r4_schema(db_path)
