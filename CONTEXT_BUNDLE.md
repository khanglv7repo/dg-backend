# Context Bundle — Current Architecture

This bundle intentionally contains the authoritative v0.6.1 context only.


<!-- BEGIN FILE: 00_START_HERE.md -->

# Start Here — Context Routing

## Stable system summary

- OpenMetadata is the metadata and confirmed-tag source of truth.
- `governance_app/` is the FastAPI Control API plus Execution Worker.
- PostgreSQL `governance_jobs` is the durable work queue.
- PostgreSQL `governance_policies` is the runtime desired-state store for Ranger policies.
- Native Ranger JSON is the policy import contract.
- Apache Ranger is the enforcement target.
- Trino is governed by Ranger, but backend-side Trino verification is not part of the production control plane.
- Policy sync is explicit; application startup does not seed YAML or auto-sync Ranger.
- Future AI/MCP integrations are thin adapters over application services and do not receive Ranger credentials.
- Missing identity headers grant no roles.

## Read-by-task map

| Task | Required context |
|---|---|
| Classification | `01`, `04`, `06`, `08`, `20`, `22` |
| OpenMetadata tag/Suggestion writes | `04`, `06`, `08`, `20` |
| Ranger policy catalog/reconciliation | `04`, `06`, `08`, `20`, `22` |
| Ranger tag assignments | `04`, `06`, `08`, `20` |
| Jobs/retry/worker | `06`, `08`, `20`, `22` |
| Authentication/identity | `06`, `20` |
| Future MCP | `06`, `08`, `20` |
| Major architecture change | read all relevant docs and update ADR + `22_ACTIVE_TASK.md` |

## Stop conditions

Do not introduce a change that:

- makes YAML/files the runtime policy source of truth;
- lets HTTP/MCP/AI mutate Ranger directly;
- overwrites unmanaged Ranger policies;
- treats absence of a DB policy row as authorization to delete Ranger state;
- grants admin/operator roles when identity headers are missing;
- adds production Trino verification back into the core control plane without a new ADR;
- invents a second policy DSL when native Ranger JSON is sufficient.

<!-- END FILE: 00_START_HERE.md -->


<!-- BEGIN FILE: 01_PRODUCT_CONTEXT.md -->

# Product and Business Context

## Objective

Provide a small governance control plane that classifies assets using OpenMetadata metadata and manages desired Apache Ranger policies without replacing either OpenMetadata or Ranger.

## System responsibilities

1. OpenMetadata owns asset metadata, confirmed tags, Suggestions and reviewer workflows.
2. Deterministic classification evaluates current OpenMetadata metadata using versioned rules.
3. PostgreSQL owns desired Ranger policy state.
4. Ranger owns enforcement.
5. Trino is the governed query engine behind Ranger; backend-side query verification is not a production capability.

## Product boundary

The backend adds:

- deterministic exact/regex classification;
- native OpenMetadata Suggestion creation;
- explicitly trusted direct tag application;
- confirmed-tag synchronization into Ranger's tag store;
- native Ranger JSON policy import and desired-state storage;
- explicit DB -> Ranger reconciliation;
- durable jobs, idempotency and audit evidence.

The backend does not add:

- a second metadata catalog;
- a custom human review UI;
- a custom policy language;
- automatic policy generation from OpenMetadata tags;
- startup policy mutation;
- production Trino verification jobs.

## Success criteria

- OpenMetadata and Ranger remain authoritative in their respective domains.
- Repeated policy import is idempotent and revisioned.
- Explicit policy sync converges DB desired state to owned Ranger policies.
- Unmanaged Ranger policies are never overwritten implicitly.
- Missing identity headers grant no governance roles.
- The core remains understandable as MVC + Service + Repository.

<!-- END FILE: 01_PRODUCT_CONTEXT.md -->


<!-- BEGIN FILE: 04_BUSINESS_RULES.md -->

# Business Rules

## Classification

BR-001. Deterministic exact/regex rules evaluate current OpenMetadata metadata.

BR-002. Manual classification commands contain target identity only; the worker hydrates metadata from OpenMetadata.

BR-003. A result is ambiguous when one target receives multiple distinct governed tags.

BR-004. Trusted direct auto-apply requires an `EXACT` deterministic result, no ambiguity, every contributing rule marked `auto_apply: true`, and `TRUSTED_AUTO_APPLY_ENABLED=true`.

BR-005. Untrusted/uncertain classifications use native OpenMetadata Suggestions.

BR-006. OpenMetadata remains the source of truth for metadata and confirmed tags.

## Ranger policy catalog

BR-020. PostgreSQL `governance_policies` is the only runtime desired-state source for Ranger policies.

BR-021. Policy import accepts native Ranger policy JSON; no parallel policy DSL is introduced.

BR-022. TAG policies target the configured Ranger tag service and contain only the `tag` resource.

BR-023. RESOURCE policies target the configured Ranger resource service and must not contain the `tag` resource.

BR-024. Only policies carrying the backend ownership marker may be updated.

BR-025. Ranger dry-run is the safety default.

BR-026. Disable/delete is explicit. Missing DB rows never authorize live Ranger deletion.

BR-027. Policy import and policy sync are separate operations.

BR-028. Backend startup does not seed policy YAML and does not enqueue automatic policy reconciliation.

## Ranger tag assignment

BR-030. Confirmed OpenMetadata tag FQNs synchronize to Ranger using the same tag names.

BR-031. Tag assignment synchronization is independent of access-policy desired state.

BR-032. Tag synchronization never creates access policies.

## Identity

BR-040. Runtime automation uses machine/service identities rather than employee credentials.

BR-041. Missing actor headers grant no roles.

BR-042. AI/MCP-facing components do not receive Ranger service credentials.

BR-043. Ranger mutation remains an Execution Worker capability.

## Jobs

BR-050. External side effects run through durable PostgreSQL jobs.

BR-051. `SYNC_RANGER_POLICIES` reconciles PostgreSQL desired policy state.

BR-052. `SYNC_RANGER_TAGS` reconciles confirmed OpenMetadata tag assignments.

BR-053. Retryable failures use bounded retry; exhausted/non-retryable work becomes `DEAD`.

<!-- END FILE: 04_BUSINESS_RULES.md -->


<!-- BEGIN FILE: 06_INVARIANTS.md -->

# Non-Negotiable Invariants

INV-001. OpenMetadata is the metadata and confirmed-tag source of truth.

INV-002. PostgreSQL `governance_policies` is the runtime desired-state source for Ranger policies.

INV-003. PostgreSQL `governance_jobs` is the durable production work queue.

INV-004. Native Ranger policy JSON is stored as JSON/JSONB; no custom policy DSL.

INV-005. Policy YAML is not a runtime source.

INV-006. Application startup performs no Ranger policy mutation or automatic policy sync.

INV-007. Ranger mutation is Execution Worker-only.

INV-008. HTTP, CLI, future MCP and AI adapters call/enqueue application capabilities and do not receive Ranger credentials.

INV-009. Controllers contain no business decisions or SQLAlchemy queries.

INV-010. Repositories contain persistence logic only and do not call external systems.

INV-011. Unmanaged Ranger policies are never overwritten implicitly.

INV-012. Missing desired-state rows never authorize Ranger deletion.

INV-013. OpenMetadata tag writes preserve unrelated tags and use read-back verification where controlled direct writes occur.

INV-014. Ranger dry-run is enabled by default.

INV-015. Trusted direct tag auto-apply is disabled by default.

INV-016. Missing identity headers grant no governance role.

INV-017. No production Trino verification executor/job or Trino-backed sample scanner exists in the core control plane.

INV-018. All Python imports are at file headers; inline/deferred imports are forbidden.

<!-- END FILE: 06_INVARIANTS.md -->


<!-- BEGIN FILE: 08_ARCHITECTURE.md -->

# System Architecture

```text
                    Human / CLI / future MCP
                               |
                               v
+------------------------------------------------------------------+
| Governance Backend                                               |
|                                                                  |
| FastAPI Control API                                              |
|   /classifications/run                                           |
|   /policies/import                                               |
|   /policies/sync                                                 |
|                                                                  |
| PostgreSQL                                                       |
|   governance_jobs       durable execution queue                  |
|   governance_policies   Ranger desired-state catalog             |
|                                                                  |
| Execution Worker                                                 |
|   OpenMetadata classification/tagging                            |
|   Ranger policy reconciliation                                   |
|   Ranger tag-assignment synchronization                          |
+----------------------+-------------------------+-----------------+
                       |                         |
                       v                         v
                 OpenMetadata               Apache Ranger
               metadata/tag truth          enforcement target
                                                 |
                                                 v
                                               Trino
```

## Core capability 1 — Classification

- API accepts target identity.
- Worker reads current metadata from OpenMetadata.
- `classification_rules.yaml` performs deterministic matching.
- Uncertain results use OpenMetadata Suggestions.
- Trusted deterministic rules may directly apply tags only under the explicit feature flag.
- Confirmed tags may be mirrored to Ranger's tag store.

## Core capability 2 — Policy management

- Native Ranger JSON enters through API/CLI/future MCP.
- `governance_policies` stores desired state.
- Import does not mutate Ranger.
- `/policies/sync` enqueues `SYNC_RANGER_POLICIES`.
- Worker compares DB desired state with Ranger observed state.
- Only backend-owned Ranger policies can be changed.

## Startup behavior

Startup starts the execution worker when configured. It does not seed policy YAML and does not automatically reconcile Ranger policies.

## Future MCP

Governance MCP is a thin adapter over the same application services used by REST. It may expose classification and policy-management commands. It does not implement Ranger logic and does not receive Ranger credentials.

OpenMetadata MCP remains appropriate for metadata discovery/lineage.

<!-- END FILE: 08_ARCHITECTURE.md -->


<!-- BEGIN FILE: 20_DECISIONS.md -->

# Architecture Decision Records

## ADR-001 — OpenMetadata is the metadata/tag system of record

Accepted.

## ADR-003 — MVC + Service + Repository

Accepted. Keep narrow external clients and application services; do not over-engineer the core.

## ADR-005 — PostgreSQL durable queue

Accepted. `governance_jobs` coordinates API and Execution Worker.

## ADR-006 — Machine identities

Accepted. Runtime automation does not use personal employee credentials.

## ADR-008 — Ranger remains Execution Worker-only

Accepted. HTTP/MCP/AI-facing components have no direct Ranger mutation path.

## ADR-009 — Ranger ownership marker

Accepted. `managed-by=dg-backend` remains the mutation ownership guard.

## ADR-011 — Separate OpenMetadata credentials

Accepted. Ingestion, classification-read and tag-mutation credentials remain separable.

## ADR-013 — Reuse infrastructure without restoring legacy automation

Accepted.

## ADR-014 — PostgreSQL Ranger desired-state catalog

Accepted.

- `governance_policies` is the runtime policy source of truth.
- Documents use native Ranger JSON.
- TAG and RESOURCE policies share the same catalog and reconciler.
- Import and sync are separate.
- Missing rows do not authorize Ranger deletion.
- Unmanaged policies are not overwritten.

## ADR-015 — Remove legacy policy YAML runtime and Trino verification

Accepted, 2026-08-03.

- Remove `config/policies.yaml` as a runtime/bootstrap path after DB migration.
- Remove startup policy seeding and startup auto-sync.
- Policy creation/update occurs through API/CLI/future MCP into PostgreSQL.
- Policy reconciliation is explicit through `SYNC_RANGER_POLICIES`.
- Remove the production Trino verification client/service/job/dependency.
- Remove the legacy sample-value scanner and its Trino sampling fallback from the core classification path.
- Keep Trino as the Ranger-protected query engine.
- Preserve historical DB migration/table data; no destructive audit-table drop is part of this cleanup.
- Missing identity headers grant no roles.
- A future MCP implementation must reuse backend services and keep Ranger credentials worker-only.

<!-- END FILE: 20_DECISIONS.md -->


<!-- BEGIN FILE: 22_ACTIVE_TASK.md -->

# Active Task

## Status

DB-backed Ranger policy control plane is active. Cleanup/hardening is the current task as of 2026-08-03.

## Validated before cleanup

- Alembic upgraded to `0006_governance_policy_catalog` on PostgreSQL.
- Test suite passed: `48 passed`.
- Local FastAPI startup succeeded.
- DB-backed policy sync reached local Apache Ranger with HTTP 200 updates for the migrated `dev_tag` policies.
- Ranger mutation was observed with `RANGER_DRY_RUN=false`.

## Cleanup scope

- remove policy YAML runtime/bootstrap code;
- remove startup policy auto-sync;
- remove legacy Trino verification runtime code/dependency/job;
- remove sample-value scanner/Trino sampling fallback from classification;
- require explicit actor role headers for policy read/write/sync;
- close OpenMetadata clients created by long-running worker handlers;
- reserve space for the Ranger ownership marker when truncating descriptions;
- align version/docs/capability reporting with v0.6.1.

## Intentionally retained

- existing historical Alembic migrations and DB audit tables;
- Trino as the Ranger-protected query engine;
- `RECONCILE_RANGER` compatibility job temporarily, for already-queued pre-v0.6 jobs;
- optional Agent/LLM code outside the core policy flow, pending a separate decision/cleanup.

## Validation after applying cleanup

```bash
alembic upgrade head
pytest -q
python -m compileall -q app tests
```

Then verify:

1. backend restart does not enqueue policy sync automatically;
2. `GET /api/v1/policies` without role headers returns 403;
3. explicit `POST /api/v1/policies/sync` queues a job;
4. repeated sync of converged state results in `NO_CHANGE`;
5. unmanaged same-name Ranger policies remain protected;
6. OM -> Ranger tag resource hierarchy is validated against the live `dev_trino` service definition.

<!-- END FILE: 22_ACTIVE_TASK.md -->
