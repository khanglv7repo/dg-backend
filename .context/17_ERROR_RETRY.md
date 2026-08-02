# Error and Retry Model

## Error categories

- Validation/configuration: non-retryable until configuration changes.
- Authorization/permission: non-retryable by default; investigate Bot/service role.
- External transient: retryable, such as timeout or temporary 5xx.
- External contract mismatch: non-retryable until adapter/schema is corrected.
- Conflict/idempotency: return existing logical work or fail safely.
- Verification mismatch: business failure evidence, not necessarily transport retry.

## Retry ownership

- Execution Worker retries OpenMetadata REST, Ranger, and Trino jobs.
- Agent Worker retries the `AGENT_CLASSIFY` job according to job attempts.
- Internal Agent-to-Execution hand-off does not use HTTP; enqueue occurs in the shared database transaction.

## Backoff

Retryable jobs transition to `RETRY_WAIT` with bounded exponential backoff and jitter. Exhausted attempts become `DEAD`.

## Stale recovery

Workers periodically return stale `RUNNING` jobs to `QUEUED` based on heartbeat timestamp.

## Manual retry

Only governance operator/admin API actors can retry `DEAD` or `CANCELLED` jobs and must provide a reason.

## Fail-safe examples

- Unknown Agent tag: reject; no Suggestion.
- Ranger policy not owned: do not overwrite.
- OpenMetadata read-back missing confirmed tag: fail the job.
- MCP mutation tool request: reject locally.
