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
    CLASSIFY_ASSET_FROM_OM = "CLASSIFY_ASSET_FROM_OM"
    AGENT_CLASSIFY = "AGENT_CLASSIFY"
    CREATE_OM_SUGGESTIONS = "CREATE_OM_SUGGESTIONS"
    APPLY_CONFIRMED_TAGS = "APPLY_CONFIRMED_TAGS"

    # Policy desired state is stored in PostgreSQL and reconciled by the worker.
    SYNC_RANGER_POLICIES = "SYNC_RANGER_POLICIES"

    # Confirmed OpenMetadata tags are synchronized independently into Ranger's
    # tag store. This does not create access policies.
    SYNC_RANGER_TAGS = "SYNC_RANGER_TAGS"

    # Kept so already-queued v0.4 jobs remain claimable during a local upgrade.
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
