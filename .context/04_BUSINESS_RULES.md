# Business Rules

## Classification

BR-001. Deterministic rules always run before optional fallback behavior.

BR-002. Exact/regex results are reproducible from current OpenMetadata metadata and versioned `classification_rules.yaml` configuration.

BR-003. A result is ambiguous when the same target receives more than one distinct tag.

BR-004. Trusted auto-apply requires all conditions:

- deterministic `EXACT` outcome;
- every contributing rule has `auto_apply: true`;
- no ambiguity;
- `TRUSTED_AUTO_APPLY_ENABLED=true`.

BR-005. Manual classification commands contain only the target identity. The Execution Worker reads current metadata from OpenMetadata before evaluating rules.

BR-006. OpenMetadata remains the source of truth for metadata and assigned/confirmed tags.

## OpenMetadata

BR-010. OpenMetadata owns Suggestion acceptance/rejection and reviewer experience.

BR-011. The application must not maintain a parallel proposal or approval state machine.

BR-012. Tag writes target the smallest entity possible, preferably column-by-FQN for column tags.

BR-013. Existing unrelated tags must be preserved.

BR-014. Every controlled direct write requires read-back verification.

## Ranger policy catalog

BR-020. PostgreSQL `governance_policies` is the runtime source of truth for desired Ranger policies.

BR-021. Policy import accepts native Ranger policy JSON. The backend does not define a second business policy language.

BR-022. A desired policy may target only the configured Ranger tag service or configured Ranger resource service.

BR-023. Tag policies target the tag service and contain only the `tag` resource. Resource policies target the resource service and must not contain the `tag` resource.

BR-024. The application mutates only policies it owns, identified by the `managed-by=dg-backend` marker.

BR-025. Ranger dry-run is the default and must not make mutation requests.

BR-026. Disabling/removing a desired policy is explicit. Absence of a DB row is never interpreted as permission to delete an arbitrary Ranger policy.

BR-027. `config/policies.yaml` is migration/bootstrap seed input only. After the DB catalog contains policies, normal reconciliation does not read policy YAML.

## Ranger tag assignment

BR-030. Confirmed OpenMetadata tag FQNs are synchronized to Ranger with the same tag names; there is no configurable OM-tag-to-Ranger-tag business mapping.

BR-031. Tag assignment synchronization is independent from policy desired-state synchronization.

## Identity

BR-040. Runtime components must use Bot/service identities, never personal accounts.

BR-041. OpenMetadata Agent Bot, Auto-classification Bot, Auto-tag Bot, and Ingestion Bot must be distinct machine identities.

BR-042. AI/MCP-facing components must not receive Ranger service credentials. Ranger mutation remains an Execution Worker capability.

BR-043. Execution Worker must not require an LLM provider key.

BR-044. The upstream metadata-ingestion workflow and asset discovery use `OM_INGESTION_BOT_TOKEN`; classification metadata reads use `OM_AUTOCLASSIFICATION_BOT_TOKEN`; native Suggestions, trusted tag writes, and their read-back use `OM_AUTO_TAG_BOT_TOKEN`.

## Jobs

BR-050. External side effects are executed through durable PostgreSQL jobs.

BR-051. `SYNC_RANGER_POLICIES` is the only new-policy reconciliation job for the DB-backed policy catalog.

BR-052. `SYNC_RANGER_TAGS` synchronizes confirmed OpenMetadata tag assignments and never creates access policies.

BR-053. Retryable failures use bounded exponential backoff; exhausted/non-retryable failures become `DEAD`.
