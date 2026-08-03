# Validation Report — v0.6.1 cleanup

Validation date: 2026-08-03.

## Confirmed before this cleanup

The DB-backed policy control plane was validated on the local development environment:

```text
alembic upgrade head
PASS

pytest -q
48 passed
```

Live application startup also succeeded, and the durable policy-sync worker reached local Apache Ranger with successful HTTP 200 policy updates while `RANGER_DRY_RUN=false`.

Migration head confirmed before cleanup:

```text
0006_governance_policy_catalog
```

## Cleanup changes requiring re-validation

- removal of policy YAML runtime/bootstrap;
- removal of startup policy auto-sync;
- removal of production Trino verification code/dependency/job;
- removal of sample-value scanner and Trino sampling fallback;
- policy read authorization;
- missing identity headers grant no roles;
- OpenMetadata worker-client close/finally handling;
- Ranger ownership-marker-safe description truncation;
- version/docs alignment.

Run after applying cleanup:

```bash
conda activate dg_backend
cd governance_app

alembic upgrade head
pytest -q
python -m compileall -q app tests
```

## Live checks after cleanup

1. Start the backend and confirm no `Ranger policy sync queued` message appears only because of startup.
2. Confirm unauthenticated/no-role policy read is rejected.
3. Import native Ranger JSON into PostgreSQL.
4. Explicitly call `/api/v1/policies/sync`.
5. Confirm converged state becomes `NO_CHANGE`.
6. Confirm an unmanaged same-name Ranger policy is rejected.

## Still requiring specific live validation

- exact `dev_trino` Ranger service resource hierarchy for OM -> Ranger tag resources;
- tag-based enforcement against real catalog/schema/table/column resources;
- OpenMetadata webhook permissions and Bot scopes in the final deployment;
- future MCP authentication/authorization boundary.

Trino remains the Ranger-protected query engine. Backend-side Trino verification is intentionally removed from the production control plane.
