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
