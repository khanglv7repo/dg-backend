"""Route OpenMetadata ChangeEvents only to governance runtime work.

Backend-owned classification has been retired. OpenMetadata remains the
metadata/tag authority; Backend only reacts to tag changes that must be
propagated to Ranger.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class EventPurpose(StrEnum):
    TAG_SYNC = "TAG_SYNC"


class EventPurposeRouter:
    """Pure router mapping raw OpenMetadata ChangeEvents to runtime purposes."""

    @classmethod
    def route(cls, event_data: dict[str, Any]) -> set[EventPurpose]:
        raw_event_type = str(event_data.get("eventType") or "").strip()
        event_type = raw_event_type.lower().replace("_", "")
        if not event_type:
            return set()

        # New assets have no Backend classification step. A tag sync is still
        # harmless/idempotent and lets Ranger converge if OM already assigned
        # native/manual tags during creation.
        if event_type == "entitycreated":
            return {EventPurpose.TAG_SYNC}

        change_desc = event_data.get("changeDescription") or {}
        inc_change_desc = event_data.get("incrementalChangeDescription") or {}

        if cls._has_tag_change(change_desc) or cls._has_tag_change(inc_change_desc):
            return {EventPurpose.TAG_SYNC}
        return set()

    @classmethod
    def _has_tag_change(cls, change_desc: dict[str, Any]) -> bool:
        for bucket in ("fieldsAdded", "fieldsUpdated", "fieldsDeleted"):
            changes = change_desc.get(bucket, []) or []
            for change in changes:
                if not isinstance(change, dict):
                    continue

                name = str(change.get("name") or "").lower()
                if "tag" in name:
                    return True

                if cls._contains_tag_payload(change.get("oldValue")):
                    return True
                if cls._contains_tag_payload(change.get("newValue")):
                    return True
        return False

    @classmethod
    def _contains_tag_payload(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, nested in value.items():
                if "tag" in str(key).lower():
                    return True
                if cls._contains_tag_payload(nested):
                    return True
            return False

        if isinstance(value, list):
            return any(cls._contains_tag_payload(item) for item in value)

        if isinstance(value, str):
            lowered = value.lower()
            return "tagfqn" in lowered or '"tags"' in lowered or "taglabels" in lowered

        return False
