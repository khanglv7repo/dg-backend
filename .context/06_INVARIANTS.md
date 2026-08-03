# Non-Negotiable Invariants

INV-001. OpenMetadata is the metadata and tag system of record.

INV-002. The backend (`governance_app`) is the FastAPI Control API and Execution Worker. Optional AI/MCP components are adapters/clients of backend application capabilities; they do not own Ranger business logic.

INV-003. PostgreSQL `governance_jobs` is the production work queue until an ADR changes this.

INV-004. PostgreSQL `governance_policies` is the runtime desired-state store for Ranger policies.

INV-005. Native Ranger policy JSON is persisted as JSON/JSONB. Do not invent a parallel policy DSL unless an ADR explicitly requires one.

INV-006. `config/policies.yaml` is seed/migration input only after the DB-backed catalog is introduced.

INV-007. Ranger mutation is Execution Worker-only. API routes, MCP tools, and AI agents enqueue work and never hold Ranger credentials.

INV-008. OpenMetadata tag writes remain controlled backend operations and preserve unrelated tags.

INV-009. Controllers contain no business decisions or SQLAlchemy queries.

INV-010. Repositories contain persistence logic only; they do not call external systems.

INV-011. External API payloads are validated/normalized at service/client boundaries before persistence or mutation.

INV-012. No raw business query result rows are stored.

INV-013. No custom proposal/approval tables or reviewer UI.

INV-014. Ranger dry-run is enabled by default.

INV-015. Trusted auto-apply is disabled by default.

INV-016. The Ranger ownership marker `managed-by=dg-backend` is retained. An existing unmanaged Ranger policy must not be overwritten implicitly.

INV-017. All Python imports across all projects are placed at file headers; inline/deferred imports are forbidden.

INV-018. Missing desired-state rows do not authorize destructive Ranger cleanup. Disable/delete is explicit and auditable.
