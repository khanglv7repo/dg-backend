# Active Task

## Status

Legacy platform migration is in progress as of 2026-07-31.

## Change

Migrate reusable Docker lab, metadata ingestion, Faker, DQ, and bootstrap assets from `old` into the current repository while refactoring them to the current backend/agent and Bot-identity boundaries.

## Implemented

- decoupled Agent code into standalone project `governance_agent/`;
- created `governance_agent` project structure (`pyproject.toml`, `README.md`, `app/`, `tests/`);
- connected `governance_agent` directly to OpenMetadata via MCP client & REST Suggestion API;
- removed in-tree `app/agent` module and `agent_worker.py` from `governance_app/`;
- updated `governance_app` pyproject.toml and Makefile;
- recorded ADR-010 in `20_DECISIONS.md`;
- updated documentation (`AGENTS.md`, `.context/00_START_HERE.md`, `.context/08_ARCHITECTURE.md`, `.context/20_DECISIONS.md`);
- verified both test suites (32 tests in `governance_app`, 3 tests in `governance_agent`) pass cleanly.
- routed catalog discovery through `OM_INGESTION_BOT_TOKEN`;
- routed classification/sample metadata reads through `OM_AUTOCLASSIFICATION_BOT_TOKEN`;
- routed native Suggestions, trusted tag writes, and read-back through `OM_AUTO_TAG_BOT_TOKEN`;
- retained `OPENMETADATA_EXECUTION_BOT_TOKEN` as a migration alias and added three-Bot configuration tests;
- recorded ADR-011 and updated credential/security contracts.
- verified 36 `governance_app` unit/contract tests pass in the `dg_backend` environment.
- added a standalone Docker metadata-ingestion sidecar reusing the existing lab network and official OpenMetadata ingestion image;
- removed the legacy admin-token/admin-login fallback from the migrated ingestion runner and recorded ADR-012.
- replaced the live `metadata-ingestion` container; it is healthy and its first Bot-token ingestion completed with 0 errors.
- inventoried the legacy project and recorded retained versus superseded capabilities in `platform/LEGACY_MIGRATION.md` and ADR-013.
- migrated the Docker infrastructure into `platform/docker-compose.yml` and `platform/docker/`; the current FastAPI backend and legacy scripts remain separate.

## Validation pending in live environment

- actual OpenMetadata Bot permissions;
- live ingest/discovery read using `OM_INGESTION_BOT_TOKEN`, classification read using `OM_AUTOCLASSIFICATION_BOT_TOKEN`, and tag mutation/read-back using `OM_AUTO_TAG_BOT_TOKEN`;
- live OpenMetadata webhook payload event subscription;
- live Ranger policy API endpoint behavior;
- live Trino query execution & policy propagation.
