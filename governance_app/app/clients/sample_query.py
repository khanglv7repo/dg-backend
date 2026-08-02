from __future__ import annotations

import logging
from typing import Any

import trino

from app.clients.openmetadata import OpenMetadataClient
from app.clients.trino import TrinoDBAPIExecutor
from app.core.config import Settings

logger = logging.getLogger(__name__)


class SampleQueryClient:
    """Safe bounded sample query client.

    Retrieves sample values for unclassified string columns using bounded queries
    or OpenMetadata entity profile samples. Does not store or log raw sample values.
    """

    def __init__(self, settings: Settings, om_client: OpenMetadataClient | None = None) -> None:
        self.settings = settings
        self.om_client = om_client

    def fetch_column_samples(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        column_name: str,
        max_rows: int = 500,
    ) -> list[str]:
        # 1. First try OpenMetadata profile sample data if om_client available
        if self.om_client:
            try:
                table_data = self.om_client.get_entity(
                    entity_type=entity_type, fqn=entity_fqn, fields="sampleData"
                )
                sample_data = table_data.get("sampleData", {})
                cols = sample_data.get("columns", [])
                rows = sample_data.get("rows", [])
                if column_name in cols and rows:
                    col_idx = cols.index(column_name)
                    samples = [
                        str(row[col_idx])
                        for row in rows
                        if len(row) > col_idx and row[col_idx] is not None
                    ]
                    if samples:
                        return samples[:max_rows]
            except Exception as exc:
                logger.debug(f"OpenMetadata sampleData not available for {entity_fqn}: {exc}")

        # 2. Fall back to Trino query if Trino is enabled
        if self.settings.trino_enabled:
            executor = TrinoDBAPIExecutor(
                host=self.settings.trino_host,
                port=self.settings.trino_port,
                catalog=self.settings.trino_catalog,
                schema=self.settings.trino_schema,
                http_scheme=self.settings.trino_http_scheme,
                timeout_seconds=self.settings.sample_scan_timeout_seconds,
            )
            parts = entity_fqn.split(".")
            sql_table = f'"{parts[-2]}"."{parts[-1]}"' if len(parts) >= 2 else f'"{entity_fqn}"'
            sql = f'SELECT "{column_name}" FROM {sql_table} WHERE "{column_name}" IS NOT NULL LIMIT {max_rows}'
            try:
                obs = executor.execute(
                    identity=self.settings.trino_verification_service_user, sql=sql
                )
                conn = trino.dbapi.connect(
                    host=self.settings.trino_host,
                    port=self.settings.trino_port,
                    user=self.settings.trino_verification_service_user,
                    catalog=self.settings.trino_catalog,
                    schema=self.settings.trino_schema,
                    http_scheme=self.settings.trino_http_scheme,
                    request_timeout=self.settings.sample_scan_timeout_seconds,
                )
                try:
                    cur = conn.cursor()
                    cur.execute(sql)
                    rows = cur.fetchmany(max_rows)
                    samples = [str(r[0]) for r in rows if r and r[0] is not None]
                    return samples
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning(f"Trino sample query failed for {entity_fqn}.{column_name}: {exc}")

        return []
