# Integration Contracts

## OpenMetadata REST

Execution Worker uses `OM_AUTO_TAG_BOT_TOKEN` for:

- native `SuggestTagLabel` creation;
- table/entity reads;
- column-by-FQN GET/PUT;
- confirmed tag read-back.

Production enablement requires verification against the deployed 1.13 OpenAPI and permissions.

`OM_AUTOCLASSIFICATION_BOT_TOKEN` is used for OpenMetadata metadata reads needed
by classification/sample scanning and is never passed to mutation calls.

## OpenMetadata ingest/discovery REST

The upstream `openmetadata/ingestion` sidecar and Execution Worker use
`OM_INGESTION_BOT_TOKEN` for catalog ingestion and watermark-based discovery.
The sidecar passes it as the metadata-rest JWT and has no admin-login fallback.
It is not passed to Suggestion or controlled tag-write calls.

## OpenMetadata MCP

Agent Worker uses the Agent Bot token. Allowed tool names are hard allow-listed:

- `search_metadata`;
- `semantic_search`;
- `get_entity_details`;
- `get_entity_lineage`;
- `get_test_definitions`.

Mutation tools are rejected before network invocation.

The official OpenMetadata AI SDK may replace the direct JSON-RPC transport, but tool filtering and worker boundaries remain unchanged.

## Apache Ranger

Execution Worker uses Ranger service credentials. Reconciliation uses lookup, canonical normalization/hash, ownership marker, and create/update endpoints. Dry-run performs reads only.

## Trino

Execution Worker runs controlled verification cases. Identity propagation and deny/masking/row-filter behavior must be verified in the target deployment.

## LLM provider

Only Agent Worker receives the provider machine credential. Structured output must conform to `AgentDecision` and allowed tags. Prompts instruct the model not to claim mutations.
