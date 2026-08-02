# Examples

## Metadata event

```json
{
  "event_id": "evt-123",
  "event_type": "ENTITY_UPDATED",
  "entity_type": "table",
  "entity_fqn": "hive.sales.customers",
  "entity_name": "customers",
  "fields": [
    {"name": "email", "data_type": "varchar", "description": "Customer email"}
  ],
  "existing_tags": [],
  "correlation_id": "corr-123"
}
```

## Deterministic exact result

```json
{
  "outcome": "EXACT",
  "action": "AUTO_APPLY",
  "suggestions": [
    {
      "tag": "PII.Email",
      "field_path": "columns.email",
      "confidence": 0.99,
      "rule_id": "email-exact"
    }
  ]
}
```

## Agent structured decision

```json
{
  "suggestions": [
    {
      "tag": "PII.Email",
      "field_path": "columns.contact_address",
      "confidence": 0.87,
      "rationale": "Column description and lineage indicate customer email data."
    }
  ],
  "summary": "One governed classification is supported by MCP metadata context."
}
```

The Agent decision is persisted, then the Execution Worker creates a native Suggestion. It is not a confirmed tag.

## Bot audit actors

```text
bot:governance-agent-bot      -> AGENT_CLASSIFICATION_COMPLETED
bot:governance-execution-bot  -> OPENMETADATA_SUGGESTIONS_CREATED
alice@example.com             -> native OpenMetadata acceptance/rejection
```
