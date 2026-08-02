# Engineering Task Playbook

## Task header template

```text
Goal:
Business rule(s):
Invariant(s):
Affected controller/service/repository/client/worker:
External contract:
Migration impact:
Tests required:
Rollback/safety note:
```

## Adding a deterministic rule

1. Update YAML.
2. Confirm target, exact/regex condition, tag, confidence, rationale, and `auto_apply`.
3. Add exact, no-match, and conflict tests.
4. Update business examples if semantics change.

## Changing Agent behavior

1. Confirm it remains fallback-only.
2. Keep MCP tool list read-only.
3. Update structured schema/prompt/graph version.
4. Ensure tags are validated again after model output.
5. Add tests using fake MCP/classifier; do not require a live provider for unit tests.
6. Never import Ranger/Trino/OpenMetadata REST mutation client into Agent code.

## Adding a job type

1. Add enum.
2. Define which worker owns it.
3. Add handler and deterministic idempotency key.
4. Add claim-filter and lifecycle tests.
5. Update decision table and data model docs.

## Changing OpenMetadata mutation

1. Verify deployed OpenAPI.
2. Update narrow client method.
3. Preserve unrelated metadata.
4. Add read-back assertion.
5. Use Execution Bot only.
6. Add mocked contract test.

## Changing credentials

1. Keep identities machine-only.
2. Preserve separate Agent/Execution Bot roles.
3. Update `.env.example`, capability output, security docs, and tests.
4. Do not log values.

## Definition of done

- Code compiles.
- Unit/contract tests pass.
- Migration round-trip passes when applicable.
- API/worker smoke passes.
- Context and active task are updated.
- Live-contract limitations are stated honestly.
