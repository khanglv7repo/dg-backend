"""R3 TAG correctness repair migration

Revision ID: 0009_r3_tag_correctness
Revises: 0008_r3_tag_vertical_slice
Create Date: 2026-08-09
"""
from typing import Sequence, Union
import uuid
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_r3_tag_correctness"
down_revision: Union[str, None] = "0008_r3_tag_vertical_slice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "event_inbox" in tables:
        columns = {col["name"] for col in inspector.get_columns("event_inbox")}
        if "dispatched_purposes" not in columns:
            op.add_column(
                "event_inbox",
                sa.Column(
                    "dispatched_purposes",
                    JSON_TYPE,
                    nullable=False,
                    server_default=sa.text("'[]'"),
                ),
            )
            if bind.dialect.name != "sqlite":
                op.alter_column("event_inbox", "dispatched_purposes", server_default=None)
        if "dispatched_tasks" not in columns:
            op.add_column(
                "event_inbox",
                sa.Column(
                    "dispatched_tasks",
                    JSON_TYPE,
                    nullable=False,
                    server_default=sa.text("'{}'"),
                ),
            )
            if bind.dialect.name != "sqlite":
                op.alter_column("event_inbox", "dispatched_tasks", server_default=None)

    if "classification_executions" in tables:
        constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("classification_executions")
        }
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("classification_executions") as batch_op:
                if "uq_classification_execution_gen" in constraints:
                    batch_op.drop_constraint(
                        "uq_classification_execution_gen",
                        type_="unique",
                    )
                if "uq_classification_execution_entity_generation" not in constraints:
                    batch_op.create_unique_constraint(
                        "uq_classification_execution_entity_generation",
                        ["entity_fqn", "generation"],
                    )
        else:
            if "uq_classification_execution_gen" in constraints:
                op.drop_constraint(
                    "uq_classification_execution_gen",
                    "classification_executions",
                    type_="unique",
                )
            if "uq_classification_execution_entity_generation" not in constraints:
                op.create_unique_constraint(
                    "uq_classification_execution_entity_generation",
                    "classification_executions",
                    ["entity_fqn", "generation"],
                )

    if "classification_rule_versions" not in tables:
        op.create_table(
            "classification_rule_versions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("rule_key", sa.String(length=255), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("payload", JSON_TYPE, nullable=False),
            sa.Column("checksum", sa.String(length=64), nullable=False),
            sa.Column("declared_version", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "rule_key",
                "version",
                name="uq_classification_rule_version_key_ver",
            ),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("classification_rule_versions")}
    if "ix_classification_rule_versions_key_status" not in indexes:
        op.create_index(
            "ix_classification_rule_versions_key_status",
            "classification_rule_versions",
            ["rule_key", "status"],
            unique=False,
        )
    if "uq_classification_rule_versions_one_active" not in indexes:
        op.create_index(
            "uq_classification_rule_versions_one_active",
            "classification_rule_versions",
            ["rule_key"],
            unique=True,
            postgresql_where=sa.text("status = 'ACTIVE'"),
            sqlite_where=sa.text("status = 'ACTIVE'"),
        )

    if "classification_rule_sets" in tables:
        _backfill_rule_versions_from_legacy(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "classification_rule_versions" in tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("classification_rule_versions")}
        if "uq_classification_rule_versions_one_active" in indexes:
            op.drop_index(
                "uq_classification_rule_versions_one_active",
                table_name="classification_rule_versions",
            )
        if "ix_classification_rule_versions_key_status" in indexes:
            op.drop_index(
                "ix_classification_rule_versions_key_status",
                table_name="classification_rule_versions",
            )
        op.drop_table("classification_rule_versions")

    if "classification_executions" in tables:
        constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("classification_executions")
        }
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("classification_executions") as batch_op:
                if "uq_classification_execution_entity_generation" in constraints:
                    batch_op.drop_constraint(
                        "uq_classification_execution_entity_generation",
                        type_="unique",
                    )
                if "uq_classification_execution_gen" not in constraints:
                    batch_op.create_unique_constraint(
                        "uq_classification_execution_gen",
                        ["event_id", "entity_fqn", "generation"],
                    )
        else:
            if "uq_classification_execution_entity_generation" in constraints:
                op.drop_constraint(
                    "uq_classification_execution_entity_generation",
                    "classification_executions",
                    type_="unique",
                )
            if "uq_classification_execution_gen" not in constraints:
                op.create_unique_constraint(
                    "uq_classification_execution_gen",
                    "classification_executions",
                    ["event_id", "entity_fqn", "generation"],
                )

    if "event_inbox" in tables:
        columns = {col["name"] for col in inspector.get_columns("event_inbox")}
        if "dispatched_tasks" in columns:
            op.drop_column("event_inbox", "dispatched_tasks")
        if "dispatched_purposes" in columns:
            op.drop_column("event_inbox", "dispatched_purposes")


def _backfill_rule_versions_from_legacy(bind: sa.Connection) -> None:
    legacy_rows = bind.execute(
        sa.text(
            """
            SELECT name, declared_version, document, document_sha256, status,
                   created_by, created_at, activated_at
            FROM classification_rule_sets
            ORDER BY name, created_at, id
            """
        )
    ).mappings().all()
    if not legacy_rows:
        return

    existing_checksums = {
        (row["rule_key"], row["checksum"])
        for row in bind.execute(
            sa.text("SELECT rule_key, checksum FROM classification_rule_versions")
        ).mappings()
    }
    max_versions: dict[str, int] = {
        row["rule_key"]: int(row["max_version"] or 0)
        for row in bind.execute(
            sa.text(
                "SELECT rule_key, max(version) AS max_version "
                "FROM classification_rule_versions GROUP BY rule_key"
            )
        ).mappings()
    }

    for legacy in legacy_rows:
        rule_key = legacy["name"] or "default"
        checksum = legacy["document_sha256"]
        if (rule_key, checksum) in existing_checksums:
            continue

        max_versions[rule_key] = max_versions.get(rule_key, 0) + 1
        payload_expr = (
            "CAST(:payload AS JSONB)"
            if bind.dialect.name == "postgresql"
            else ":payload"
        )
        bind.execute(
            sa.text(
                f"""
                INSERT INTO classification_rule_versions
                    (id, rule_key, version, status, payload, checksum,
                     declared_version, created_by, created_at, activated_at)
                VALUES
                    (:id, :rule_key, :version, 'INACTIVE', {payload_expr}, :checksum,
                     :declared_version, :created_by, :created_at, NULL)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "rule_key": rule_key,
                "version": max_versions[rule_key],
                "payload": json.dumps(legacy["document"]),
                "checksum": checksum,
                "declared_version": legacy["declared_version"],
                "created_by": legacy["created_by"] or "migration",
                "created_at": legacy["created_at"],
            },
        )
        existing_checksums.add((rule_key, checksum))

    active_rows = bind.execute(
        sa.text(
            """
            SELECT name, document_sha256
            FROM classification_rule_sets
            WHERE status = 'ACTIVE'
            ORDER BY name, activated_at DESC NULLS LAST, created_at DESC
            """
        )
    ).mappings().all()

    activated: set[str] = set()
    for legacy in active_rows:
        rule_key = legacy["name"] or "default"
        if rule_key in activated:
            continue
        bind.execute(
            sa.text(
                """
                UPDATE classification_rule_versions
                SET status = 'INACTIVE'
                WHERE rule_key = :rule_key
                """
            ),
            {"rule_key": rule_key},
        )
        bind.execute(
            sa.text(
                """
                UPDATE classification_rule_versions
                SET status = 'ACTIVE', activated_at = CURRENT_TIMESTAMP
                WHERE rule_key = :rule_key AND checksum = :checksum
                """
            ),
            {"rule_key": rule_key, "checksum": legacy["document_sha256"]},
        )
        activated.add(rule_key)
