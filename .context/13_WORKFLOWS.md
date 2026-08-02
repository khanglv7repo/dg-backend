# End-to-End Workflows

## A. Trusted deterministic exact match

```text
OpenMetadata event
  -> POST /events/metadata
  -> CLASSIFY_ASSET
  -> exact trusted rule + global flag
  -> APPLY_CONFIRMED_TAGS
  -> OpenMetadata targeted write using Auto-tag Bot
  -> read-back assertion
  -> RECONCILE_RANGER
  -> VERIFY_TRINO
```

## B. Deterministic reviewed match

```text
OpenMetadata event
  -> CLASSIFY_ASSET
  -> exact non-trusted result
  -> CREATE_OM_SUGGESTIONS
  -> native OpenMetadata review by human
  -> confirmed-tag event
  -> RECONCILE_RANGER
  -> VERIFY_TRINO
```

## B0. Watermark ingest/discovery

```text
OpenMetadata ingestion sidecar using OM_INGESTION_BOT_TOKEN
  -> PostgreSQL source metadata
  -> OpenMetadata catalog
POST /integrations/openmetadata/discover
  -> DISCOVER_UNCLASSIFIED_ASSETS
  -> OpenMetadata catalog read using Ingestion Bot
  -> CLASSIFY_ASSET jobs
```

## C. Agent fallback

```text
OpenMetadata event
  -> CLASSIFY_ASSET
  -> NO_MATCH or AMBIGUOUS
  -> AGENT_CLASSIFY
  -> Agent Worker claims job
  -> read-only MCP context using Agent Bot
  -> LangGraph + structured LLM output
  -> validate governed tag allow-list
  -> persist Agent classification run
  -> CREATE_OM_SUGGESTIONS
  -> Execution Worker creates Suggestions using Auto-tag Bot
  -> human review
  -> confirmed-tag event
  -> Ranger + Trino
```

There is no Agent HTTP service and no internal HTTP submission token.

## D. Confirmed manual/native tag

```text
OpenMetadata confirmed-tag event
  -> POST /events/confirmed-tags
  -> RECONCILE_RANGER
  -> optional VERIFY_TRINO
```

## E. Drift repair

```text
confirmed OM state + YAML mapping
  -> desired Ranger policy
  -> observed Ranger policy
  -> hash comparison
  -> DRY_RUN / CREATE / UPDATE / NO_CHANGE / DRIFT_REPAIR
  -> grouped Trino verification when applicable
```
