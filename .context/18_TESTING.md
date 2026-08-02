# Testing Strategy

## Unit tests

Cover pure rules and decisions:

- exact/regex matching;
- ambiguity;
- trusted auto-apply;
- Agent fallback selection;
- policy mapping/rendering;
- Ranger normalization/hash;
- Bot identity validation.

## Repository tests

Cover:

- idempotent enqueue;
- claim once;
- worker-role job filtering;
- retry transitions;
- stale recovery where practical.

## Client contract tests

Use mocked HTTP/DB adapters to verify:

- OpenMetadata entity links and tag payloads;
- targeted column API usage;
- MCP read-only allow-list;
- Ranger dry-run makes no mutation request;
- Trino observation normalization.

## Service tests

Cover:

- deterministic action to next job;
- Agent output allow-list validation;
- native Suggestion grouping/idempotency;
- Ranger-to-verification job creation;
- verification-group completion;
- audit actor uses Bot/service identity.

## Migration validation

Run clean upgrade, downgrade where supported, and upgrade again. Historical migrations may enforce empty prototype tables.

## Smoke tests

- health/readiness;
- metadata event returns 202 and job is retrievable;
- Execution Worker processes deterministic job;
- Agent Worker job claim is isolated, using test doubles when provider packages are unavailable.

## Production contract tests

Before enabling writes, verify deployed OpenMetadata, Ranger, Trino, MCP, and identity permissions using `CAPABILITY_VERIFICATION.md`.
