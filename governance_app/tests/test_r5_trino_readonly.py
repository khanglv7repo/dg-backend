from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.clients.trino_readonly import TrinoReadonlyClient, validate_readonly_sql
from app.core.config import Settings
from app.core.errors import ValidationError


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM financial.crm.customers",
        "SHOW CATALOGS",
        "DESCRIBE financial.crm.customers",
        "EXPLAIN SELECT * FROM financial.crm.customers",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
        "SELECT 1;",
    ],
)
def test_readonly_guard_allows_explicit_read_forms(sql: str) -> None:
    assert validate_readonly_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO x VALUES (1)",
        "UPDATE x SET a = 1",
        "DELETE FROM x",
        "CREATE TABLE x(a bigint)",
        "DROP TABLE x",
        "ALTER TABLE x ADD COLUMN b bigint",
        "CALL system.runtime.kill_query('x', 'y')",
        "EXECUTE prepared_query",
        "SELECT 1; SELECT 2",
        "WITH x AS (DELETE FROM y) SELECT * FROM x",
        "EXPLAIN ANALYZE SELECT * FROM x",
    ],
)
def test_readonly_guard_rejects_mutation_and_multiple_statements(sql: str) -> None:
    with pytest.raises(ValidationError):
        validate_readonly_sql(sql)


def test_destructive_sql_rejected_before_trino_connection() -> None:
    settings = Settings(
        app_env="test",
        trino_readonly_enabled=True,
        trino_readonly_user="diagnostic-reader",
    )
    client = TrinoReadonlyClient(settings)
    client._connect = MagicMock()  # type: ignore[method-assign]

    with pytest.raises(ValidationError):
        client.query("DROP TABLE financial.crm.customers")

    client._connect.assert_not_called()


def test_result_rows_are_bounded() -> None:
    settings = Settings(
        app_env="test",
        trino_readonly_enabled=True,
        trino_readonly_user="diagnostic-reader",
        trino_readonly_max_rows=2,
    )
    client = TrinoReadonlyClient(settings)
    cursor = MagicMock()
    cursor.description = [("n",)]
    cursor.fetchmany.return_value = [(1,), (2,), (3,)]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    client._connect = MagicMock(return_value=connection)  # type: ignore[method-assign]

    result = client.query("SELECT n FROM x")

    assert result["rows"] == [[1], [2]]
    assert result["row_count_returned"] == 2
    assert result["truncated"] is True
    cursor.fetchmany.assert_called_once_with(3)
