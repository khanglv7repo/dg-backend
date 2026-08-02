from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.watermark import IntegrationWatermark


class IntegrationWatermarkRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, system_name: str, watermark_key: str) -> str | None:
        record = self.session.scalar(
            select(IntegrationWatermark).where(
                IntegrationWatermark.system_name == system_name,
                IntegrationWatermark.watermark_key == watermark_key,
            )
        )
        return record.watermark_value if record else None

    def set(self, system_name: str, watermark_key: str, watermark_value: str) -> IntegrationWatermark:
        record = self.session.scalar(
            select(IntegrationWatermark).where(
                IntegrationWatermark.system_name == system_name,
                IntegrationWatermark.watermark_key == watermark_key,
            )
        )
        if record:
            record.watermark_value = watermark_value
        else:
            record = IntegrationWatermark(
                system_name=system_name,
                watermark_key=watermark_key,
                watermark_value=watermark_value,
            )
            self.session.add(record)
        self.session.flush()
        return record
