# Delivery Phases and Non-Goals

## Phase 1 — OpenMetadata-native deterministic governance

- Keep OpenMetadata close to upstream.
- Use native OpenMetadata AI features when they are already sufficient.
- Run deterministic exact/regex classification.
- Use native Suggestions except explicitly trusted exact rules.
- Reconcile Ranger and verify through Trino.
- Agent Worker is disabled.

## Phase 2 — Standalone Agent Service (`governance_agent/`)

- Enable AI Agent fallback for no-match/ambiguity.
- Run LangGraph classification flow inside standalone `governance_agent/` project.
- Retrieve context through read-only OpenMetadata MCP Bot (`governance-agent-bot`).
- Directly submit native OpenMetadata Suggestions using Agent Bot token.
- Support parallel development across multiple AI Coding Agents (multi-LLM adapters, state persistence).

## Phase 3 — Specialist Reasoning Nodes

Specialist graph nodes developed in parallel within `governance_agent/app/reasoning/`:

- `lineage_risk.py`: Lineage-aware sensitivity risk scoring;
- `conflict_detector.py`: Conflict detection with existing tags/glossary;
- `impact_analyzer.py`: Policy impact analysis on downstream assets;
- Specialist graph nodes/subgraphs;
- OpenMetadata Workflow Definitions integration when simple Suggestion review is insufficient.

The Phase 3 Agent still has no direct enforcement authority (human review via OpenMetadata Suggestions remains mandatory).

## Current non-goals

- Forking OpenMetadata for custom LLM orchestration;
- a second FastAPI Agent application;
- microservices;
- full Hexagonal/Clean Architecture;
- CQRS or event sourcing;
- generic repository framework;
- Kafka/RabbitMQ/Redis/Celery/n8n;
- custom approval UI/state machine;
- database-backed policy authoring platform;
- automatic Agent tag confirmation.
