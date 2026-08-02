# Data Model

The application persists five business record types.

## `governance_jobs`

Durable queue fields include job type, status, priority, idempotency key, payload, attempts, availability, lock owner/time, errors, and correlation ID.

Statuses:

```text
QUEUED -> RUNNING -> SUCCEEDED
                   -> RETRY_WAIT -> RUNNING
                   -> DEAD
CANCELLED -> QUEUED only through authorized retry
```

Job types:

- `CLASSIFY_ASSET`;
- `AGENT_CLASSIFY`;
- `CREATE_OM_SUGGESTIONS`;
- `APPLY_CONFIRMED_TAGS`;
- `RECONCILE_RANGER`;
- `VERIFY_TRINO`.

## `classification_runs`

Stores deterministic or Agent source, version, outcome, action, suggestions, compact evidence, confidence, OpenMetadata Suggestion IDs, and correlation ID.

It does not store full prompts, conversations, or raw MCP responses.

## `policy_reconciliations`

Stores policy key, mapping version, desired/observed hashes, Ranger policy ID, selected action, compact result, and correlation ID.

## `access_verifications`

Stores controlled identity, expected/observed allow result, pass flag, query fingerprint, error class/message, and duration. No query rows.

## `audit_events`

Stores actor identity, action, object, correlation ID, and compact details.

## Explicitly absent

- proposal/approval tables;
- human account credentials;
- Agent checkpoint tables in baseline;
- policy authoring/versioning tables;
- raw business data.
