# Architecture Decision Records

## ADR-001 — OpenMetadata is the governance system of record

Accepted. Reuse assets, tags, Suggestions, review UI, history, Events, Apps, MCP, and Workflows.

## ADR-002 — Keep Phase 1 close to upstream OpenMetadata

Accepted. Native AI may be used when sufficient. No deep LLM modification of OpenMetadata source.

## ADR-003 — MVC + Service + Repository

Accepted. Full Hexagonal/Clean Architecture is rejected as unnecessary complexity for the current scope. Narrow interfaces remain for strategies and external clients.

## ADR-004 — Separate Backend and Agent Projects

Superseded by ADR-010.

## ADR-005 — PostgreSQL durable queue

Accepted. `governance_jobs` coordinates API and Execution Worker. No broker until measured need.

## ADR-006 — Separate machine identities

Accepted. Runtime automation never uses personal accounts.

## ADR-007 — Agent Worker is read-only toward OpenMetadata

Accepted. Agent output requiring governance mutation must cross a controlled backend/OpenMetadata boundary.

## ADR-008 — Ranger remains Execution Worker-only

Accepted. AI/MCP-facing components have no Ranger credentials or direct Ranger mutation path.

## ADR-009 — Preserve Ranger ownership marker compatibility

Accepted. Existing `managed-by=dg-backend` marker remains the mutation ownership guard.

## ADR-010 — Separate project directory for AI Agent (`governance_agent/`)

Accepted. AI dependencies remain outside the core backend.

## ADR-011 — Separate ingestion, classification-read, and tag-mutation OpenMetadata credentials

Accepted. Ingestion, classification reads, and tag mutation use separate Bot credentials.

## ADR-012 — Bot-only OpenMetadata Docker ingestion

Accepted. The ingestion sidecar uses the ingestion Bot and no admin-login fallback.

## ADR-013 — Migrate reusable legacy platform assets without restoring legacy automation

Accepted. Infrastructure assets may be reused; superseded automation is not restored.

## ADR-014 — PostgreSQL desired-state Ranger policy catalog with native Ranger JSON

Accepted.

- `governance_policies` is the runtime source of truth for desired Ranger policies.
- Imported documents use Ranger's native `RangerPolicy` JSON shape; the backend does not introduce a second policy DSL.
- TAG and RESOURCE policy kinds are inferred/validated from configured service boundaries and resources.
- `config/policies.yaml` is retained only as a one-time compatibility seed when the DB catalog is empty.
- API/MCP/CLI write desired state; only the Execution Worker reconciles desired state to Ranger.
- Policy removal is soft/explicit. Missing DB rows never authorize deletion of live Ranger policy state.
- Existing unmanaged Ranger policies are not implicitly adopted or overwritten.
- This boundary is intentionally tool-ready so a future Governance MCP server can call the same application services without obtaining Ranger credentials.
