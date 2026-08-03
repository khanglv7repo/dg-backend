from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    DEAD = "DEAD"
    CANCELLED = "CANCELLED"


class JobType(StrEnum):
    CLASSIFY_ASSET = "CLASSIFY_ASSET"
    AGENT_CLASSIFY = "AGENT_CLASSIFY"
    CREATE_OM_SUGGESTIONS = "CREATE_OM_SUGGESTIONS"
    APPLY_CONFIRMED_TAGS = "APPLY_CONFIRMED_TAGS"

    # New two-flow Ranger architecture.
    # Flow A is reconciled synchronously on backend startup from config/policies.yaml.
    # Flow B is event-driven and only syncs Confirmed OM tags to Ranger's tag store.
    SYNC_RANGER_TAGS = "SYNC_RANGER_TAGS"

    # Kept so already-queued v0.4 jobs remain claimable during a local/rolling upgrade.
    # The handler now delegates to SYNC_RANGER_TAGS semantics.
    RECONCILE_RANGER = "RECONCILE_RANGER"

    VERIFY_TRINO = "VERIFY_TRINO"
    DISCOVER_UNCLASSIFIED_ASSETS = "DISCOVER_UNCLASSIFIED_ASSETS"
    SAMPLE_COLUMN_VALUES = "SAMPLE_COLUMN_VALUES"


class ClassificationSource(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AGENT = "AGENT"
    VALUE_SCANNER = "VALUE_SCANNER"


class ClassificationAction(StrEnum):
    NONE = "NONE"
    OPENMETADATA_SUGGESTION = "OPENMETADATA_SUGGESTION"
    AUTO_APPLY = "AUTO_APPLY"
    AGENT_FALLBACK = "AGENT_FALLBACK"
    SAMPLE_VALUE_FALLBACK = "SAMPLE_VALUE_FALLBACK"


class ReconciliationAction(StrEnum):
    DRY_RUN = "DRY_RUN"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    NO_CHANGE = "NO_CHANGE"
    DRIFT_REPAIR = "DRIFT_REPAIR"
    DISABLE = "DISABLE"
    DELETE = "DELETE"
    FAILED = "FAILED"
