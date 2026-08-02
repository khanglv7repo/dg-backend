Repository root contains two main project directories: `governance_app/` (Backend) and `governance_agent/` (AI Agent).

## 1. Backend Project (`governance_app/`)

```text
governance_app/
├── main.py                     FastAPI composition and exception mapping
├── app/
│   ├── api/
│   │   ├── router.py           Controller registration
│   │   ├── dependencies.py     DB/settings/actor dependencies
│   │   └── routes/             MVC controllers
│   ├── core/                   Config, errors, logging, security
│   ├── db/                     Database declarative base & sessions
│   ├── models/                 SQLAlchemy models and enums
│   ├── schemas/                Request/response/domain DTOs
│   ├── repositories/           Persistence-only operations
│   ├── rules/                  Deterministic classification & policy mapping
│   ├── services/               Intake, classification, policy sync, verification
│   ├── clients/                OpenMetadata REST, Ranger REST, Trino clients
│   ├── jobs/                   Handlers, dispatcher, processor
│   └── workers/
│       ├── base.py             Worker claim loop
│       └── execution_worker.py Execution Worker entrypoint
└── tests/                      Unit and contract tests
```

## 2. Standalone Agent Project (`governance_agent/`)

```text
governance_agent/
├── pyproject.toml              Agent package configuration & dependencies
├── README.md                   Project overview
├── app/
│   ├── main.py                 Service entrypoint
│   ├── runner.py               GovernanceAgentRunner
│   ├── classifier.py           Structured LLM classifier
│   ├── graph.py                LangGraph classification flow
│   ├── schemas.py              Agent DTOs & decisions
│   └── clients/
│       ├── mcp.py              Read-only OpenMetadata MCP JSON-RPC client
│       └── openmetadata.py     REST client for submitting Suggestions
└── tests/                      Unit tests for Agent graph and MCP client
```

## Change locations

- Add HTTP endpoint: `governance_app/app/api/routes`, `schemas`, relevant `service`, tests.
- Change business decision: relevant `service`, decision tables, tests.
- Change database query/state: `repository` and model/migration if required.
- Change external payload: relevant `client`, contract tests, capability verification.
- Change LangGraph / Agent logic: `governance_agent/app/graph.py`, `governance_agent/app/classifier.py`, Agent tests.
- Change worker ownership: `models/enums.py`, `repositories/jobs.py`, workers, decision table, tests.

## Forbidden shortcuts

- Route directly calling `Session.query`/`select`.
- Repository calling HTTP/LLM/Trino.
- Client deciding approval or auto-apply.
- Agent code importing Ranger/Trino clients.
