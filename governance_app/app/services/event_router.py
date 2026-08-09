"""EventPurposeRouter determines whether an OpenMetadata event triggers classification, tag sync, both, or none.

Routing Rules:
- Tag-only change -> {TAG_SYNC} (MUST NOT re-run classification to prevent event loops!)
- Classification-input change (description, name, dataType) -> {CLASSIFY}
- Column structural change (add/remove/rename column) -> {CLASSIFY, TAG_SYNC}
- Mixed structural + tag change -> {CLASSIFY, TAG_SYNC}
- Mixed classification-input + tag change -> {CLASSIFY, TAG_SYNC}
- Unrelated metadata (owner, followers, extension) -> {}
- entityCreated -> {CLASSIFY, TAG_SYNC}
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class EventPurpose(StrEnum):
    CLASSIFY = "CLASSIFY"
    TAG_SYNC = "TAG_SYNC"


class EventPurposeRouter:
    """Pure router mapping raw OpenMetadata ChangeEvent structures to logical EventPurposes."""

    CLASSIFICATION_FIELDS = frozenset(
        {"description", "name", "datatype", "data_type", "sampledata", "sample_values"}
    )

    @classmethod
    def route(cls, event_data: dict[str, Any]) -> set[EventPurpose]:
        raw_event_type = str(event_data.get("eventType") or "").strip()
        event_type = raw_event_type.lower().replace("_", "")
        if not event_type:
            return set()

        if event_type == "entitycreated":
            return {EventPurpose.CLASSIFY, EventPurpose.TAG_SYNC}

        change_desc = event_data.get("changeDescription") or {}
        inc_change_desc = event_data.get("incrementalChangeDescription") or {}

        has_tag = cls._has_tag_change(change_desc) or cls._has_tag_change(inc_change_desc)
        has_column_struct = cls._has_column_structural_change(change_desc) or cls._has_column_structural_change(inc_change_desc)
        has_classify_input = cls._has_classification_input_change(change_desc) or cls._has_classification_input_change(inc_change_desc)

        purposes: set[EventPurpose] = set()

        if has_column_struct:
            purposes.add(EventPurpose.CLASSIFY)
            purposes.add(EventPurpose.TAG_SYNC)

        if has_classify_input:
            purposes.add(EventPurpose.CLASSIFY)

        if has_tag:
            purposes.add(EventPurpose.TAG_SYNC)

        return purposes

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
    def _has_column_structural_change(cls, change_desc: dict[str, Any]) -> bool:
        for bucket in ("fieldsAdded", "fieldsUpdated", "fieldsDeleted"):
            changes = change_desc.get(bucket, []) or []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                name = str(change.get("name") or "").lower()
                if name in ("columns", "schema", "tables"):
                    return True
        return False

    @classmethod
    def _has_classification_input_change(cls, change_desc: dict[str, Any]) -> bool:
        for bucket in ("fieldsAdded", "fieldsUpdated", "fieldsDeleted"):
            changes = change_desc.get(bucket, []) or []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                name = str(change.get("name") or "").lower()
                if any(field in name for field in cls.CLASSIFICATION_FIELDS):
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
