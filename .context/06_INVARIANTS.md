# Non-Negotiable Invariants

INV-001. OpenMetadata is the metadata and review system of record.

INV-002. The backend (`governance_app`) is the FastAPI application for Control API and Execution Worker. The AI Agent (`governance_agent`) is a separate standalone project connected directly to OpenMetadata.

INV-003. PostgreSQL `governance_jobs` is the only production work queue until an ADR changes this.

INV-004. No runtime component uses personal human credentials.

INV-005. Agent, Auto-classification, Auto-tag, and Ingestion OpenMetadata Bot identities are different.

INV-005a. The Ingestion Bot is used only for upstream catalog ingestion and
discovery. The
Auto-classification Bot is used for classification reads, and the Auto-tag Bot is
used for OpenMetadata mutations; all configured Execution Worker Bot tokens must
not be equal.

INV-006. Agent Worker has read-only MCP access and no Ranger/Trino/mutation credentials.

INV-007. Agent output cannot enter `APPLY_CONFIRMED_TAGS`, `RECONCILE_RANGER`, or `VERIFY_TRINO` directly.

INV-008. Agent output always requires native OpenMetadata review.

INV-009. Controllers contain no business decisions or SQLAlchemy queries.

INV-010. Repositories contain persistence logic only; they do not call external systems.

INV-011. External API payloads are normalized by clients/services before persistence.

INV-012. No raw business query result rows are stored.

INV-013. No custom proposal/approval tables or reviewer UI.

INV-014. Ranger dry-run is enabled by default.

INV-015. Trusted auto-apply is disabled by default.

INV-016. The legacy Ranger ownership marker `managed-by=dg-backend` is retained for compatibility unless a migration plan is accepted.

INV-017. All Python imports across all projects must be placed at the top header of code files; inline or deferred imports inside functions, methods, or code blocks are strictly forbidden.
