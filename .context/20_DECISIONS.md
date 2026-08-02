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

Accepted. Agent Bot and Execution Bot are distinct; runtime automation never uses personal accounts.

## ADR-007 — Agent Worker is read-only toward OpenMetadata

Accepted. MCP mutation tools are excluded; Agent output always requires native review.

## ADR-008 — Ranger and Trino remain Execution Worker-only

Accepted. Agent has no credentials or direct path.

## ADR-009 — Preserve Ranger ownership marker compatibility

Accepted. Existing `managed-by=dg-backend` marker remains until a safe live-policy migration is designed.

## ADR-010 — Separate project directory for AI Agent (`governance_agent/`)

Accepted. The AI Agent is decoupled from `governance_app` backend into a standalone project `governance_agent/`. The agent connects directly to OpenMetadata via MCP for discovery and REST for native Suggestions.

## ADR-011 — Separate ingestion, classification-read, and tag-mutation OpenMetadata credentials

Accepted. The Execution Worker uses `OM_INGESTION_BOT_TOKEN` only for catalog
discovery/ingest reads, `OM_AUTOCLASSIFICATION_BOT_TOKEN` only for classification
metadata reads, and `OM_AUTO_TAG_BOT_TOKEN` for native Suggestion/tag mutation
actions. `OPENMETADATA_EXECUTION_BOT_TOKEN` remains an input alias during
migration, but new deployments use the three explicit variables.

## ADR-012 — Bot-only OpenMetadata Docker ingestion

Accepted. Reuse the official `openmetadata/ingestion` image from the existing
Docker lab through a small compose file in `governance_app/`. Its runner uses
`OM_INGESTION_BOT_TOKEN` directly and has no admin JWT, admin PAT, or credential
login fallback. The old full Docker stack is not copied into this repository.

## ADR-013 — Migrate reusable legacy platform assets without restoring legacy automation

Accepted. Only the Docker lab and its bootstrap configuration from the legacy
project are migrated under `platform/` and adapted to the current environment
contracts. All legacy scripts and the legacy governance automation FastAPI
application are excluded because they are not infrastructure and would duplicate
or violate the current backend/agent, durable-job, native-review, and
machine-identity boundaries.
