# Business Rules

## Classification

BR-001. Deterministic exact/regex rules evaluate current OpenMetadata metadata.

BR-002. Manual classification commands contain target identity only; the worker hydrates metadata from OpenMetadata.

BR-003. A result is ambiguous when one target receives multiple distinct governed tags.

BR-004. Trusted direct auto-apply requires an `EXACT` deterministic result, no ambiguity, every contributing rule marked `auto_apply: true`, and `TRUSTED_AUTO_APPLY_ENABLED=true`.

BR-005. Untrusted/uncertain classifications use native OpenMetadata Suggestions.

BR-006. OpenMetadata remains the source of truth for metadata and confirmed tags.

BR-007. Before creating native Suggestions, the worker validates every candidate tag FQN against OpenMetadata; a missing taxonomy tag fails the entire batch before any Suggestion write.

BR-008. Before creating a native Suggestion, the worker reads the live entity once and skips a tag already present on the same entity or column with `Suggested` or `Confirmed` state.

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
