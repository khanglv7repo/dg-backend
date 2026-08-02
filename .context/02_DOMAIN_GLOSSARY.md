# Domain Glossary

| Term | Meaning in this project |
|---|---|
| Asset | OpenMetadata entity such as a table or dashboard data model. |
| Field | A column or other child field identified by a field path such as `columns.email`. |
| Classification | A governed OpenMetadata tag taxonomy. |
| Confirmed tag | A tag whose OpenMetadata `state` is `Confirmed`. |
| Suggested tag | A tag proposed through OpenMetadata Suggestions and not yet confirmed. |
| Deterministic rule | Exact or regex rule whose result is reproducible from normalized metadata. |
| Trusted auto-apply | Explicit deterministic exact rule allowed to write a confirmed tag under a global feature flag. |
| Agent fallback | Phase 2–3 LangGraph reasoning used for `NO_MATCH` or `AMBIGUOUS` deterministic outcomes. |
| Native Suggestion | OpenMetadata `SuggestTagLabel` object reviewed in OpenMetadata. |
| Desired policy | Canonical Ranger policy document produced from confirmed tags and YAML mappings. |
| Reconciliation | Compare desired and observed Ranger state, then choose dry-run/create/update/no-change/drift repair. |
| Verification case | Controlled Trino query, identity, and expected allow/deny result. |
| Bot | OpenMetadata machine identity represented as a special user for automation. |
| Service identity | Non-human Ranger or Trino credential used by a runtime component. |
| Control API | The single FastAPI HTTP application that accepts events and exposes job/run status. |
| Execution Worker | Worker allowed to call OpenMetadata REST mutations, Ranger, and Trino. |
| Agent Worker | Optional worker allowed to call read-only OpenMetadata MCP and an LLM provider. |
| Job | Durable unit in PostgreSQL `governance_jobs`. |
| Correlation ID | Identifier joining events, jobs, classification runs, reconciliations, and verification records. |
| Idempotency key | Deterministic key preventing duplicate logical work. |
