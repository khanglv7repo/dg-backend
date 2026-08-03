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
