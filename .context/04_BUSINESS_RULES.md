# Business Rules

## Classification

BR-001. Deterministic rules always run before Agent fallback.

BR-002. Exact/regex results are reproducible from the normalized event and versioned YAML configuration.

BR-003. A result is ambiguous when the same target receives more than one distinct tag.

BR-004. Trusted auto-apply requires all conditions:

- deterministic `EXACT` outcome;
- every contributing rule has `auto_apply: true`;
- no ambiguity;
- `TRUSTED_AUTO_APPLY_ENABLED=true`.

BR-005. Agent fallback may run only when `AGENT_ENABLED=true` and the deterministic outcome is `NO_MATCH` or `AMBIGUOUS`.

BR-006. Agent output is constrained to the configured governed tag allow-list.

BR-007. Agent output never auto-applies. Non-empty Agent output always becomes native OpenMetadata Suggestions.

BR-008. Empty Agent output creates a completed classification run with no mutation job.

## OpenMetadata

BR-010. OpenMetadata owns Suggestion acceptance/rejection and reviewer experience.

BR-011. The application must not maintain a parallel proposal or approval state machine.

BR-012. Tag writes target the smallest entity possible, preferably column-by-FQN for column tags.

BR-013. Existing unrelated tags must be preserved.

BR-014. Every controlled direct write requires read-back verification.

## Ranger

BR-020. Ranger policies are generated only from confirmed OpenMetadata tags.

BR-021. One tag must resolve to zero or exactly one active mapping. More than one mapping is configuration error.

BR-022. A mapping may generate one policy per tagged column.

BR-023. The application mutates only policies it owns, identified by its management marker.

BR-024. Ranger dry-run is the default and must not make mutation requests.

BR-025. No matching enforcement mapping is a valid classified-but-unenforced outcome and must be audited.

## Trino

BR-030. Verification occurs only after a non-dry-run Ranger reconciliation that can affect policy state.

BR-031. Verification stores query fingerprints and outcomes, never business result rows.

BR-032. A verification group completes when all expected cases are recorded.

## Identity

BR-040. Runtime components must use Bot/service identities, never personal accounts.

BR-041. OpenMetadata Agent Bot, Auto-classification Bot, Auto-tag Bot, and Ingestion Bot must be distinct machine identities.

BR-042. Agent Worker must not receive Ranger, Trino, or OpenMetadata mutation credentials.

BR-043. Execution Worker must not require an LLM provider key.

BR-044. The upstream metadata-ingestion workflow and asset discovery use `OM_INGESTION_BOT_TOKEN`; classification metadata reads use `OM_AUTOCLASSIFICATION_BOT_TOKEN`; native Suggestions, trusted tag writes, and their read-back use `OM_AUTO_TAG_BOT_TOKEN`. No ingestion flow may use an admin token or admin-login fallback.

## Jobs

BR-050. Every logical work item has a deterministic idempotency key.

BR-051. Agent Worker claims only `AGENT_CLASSIFY`.

BR-052. Execution Worker excludes `AGENT_CLASSIFY`.

BR-053. Retryable failures use bounded exponential backoff; exhausted/non-retryable failures become `DEAD`.
