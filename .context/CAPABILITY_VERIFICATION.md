# Live Capability Verification

Complete before enabling production writes.

## OpenMetadata identities

- Create `governance-agent-bot` and assign read-only MCP permissions.
- Create `governance-execution-bot` and assign only required REST permissions.
- Confirm Bot tokens are distinct and no worker uses a human token.
- Confirm Execution Bot cannot accept Suggestions as reviewer.

## OpenMetadata REST

- Verify Suggestion payload/entity-link format.
- Verify column-by-FQN GET/PUT and tag merge behavior.
- Verify confirmed-tag event shape after acceptance.
- Verify read-back state and change-source behavior.

## MCP / AI SDK

- Verify endpoint, auth, transport, and actual tool names.
- Confirm mutation tools are not exposed to Agent Bot or are filtered.
- Verify `get_entity_details`/`get_entity_lineage` arguments.
- Verify installed `data-ai-sdk` version against deployed OpenMetadata.

## Agent runtime

- Install LangGraph/provider extras.
- Validate structured output and empty-result behavior.
- Confirm container has no Ranger, Trino, or Execution Bot secret.

## Ranger

- Verify service account, policy lookup/create/update, resources, access types, masking/row filters, ownership marker, and propagation delay.

## Trino

- Verify controlled service identities, impersonation, deny semantics, masking, row filters, timeout, and Ranger propagation.
