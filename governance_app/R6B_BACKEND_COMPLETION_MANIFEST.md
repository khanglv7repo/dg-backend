# R6-B Backend Completion Channel Overlay

Baseline repository: `khanglv7repo/dg-backend`

Required baseline commit:

`722e56293332dadf77e37cc4cd45df9c8e6b52f0`

This overlay intentionally crosses the R5 -> R6-B phase boundary by extending the
Backend FastMCP contract from the frozen 15 R5 tools to 16 tools. The one new tool is:

`complete_classification_execution`

Semantics:

- accepts only `execution_id + generation + COMPLETED|NO_PROPOSAL + bounded result`
- locks the execution row for the completion state transition
- stale/superseded generation: zero authority change
- only a current `WAITING_AI` generation may transition
- semantic duplicate completion: idempotent `NO_CHANGE` (`authority_changed=false`)
- conflicting duplicate terminal result: fail closed with `CONFLICT`
- preserves the deterministic fallback `outcome`; final AI state is represented by `status`
- persists bounded APPLY recommendations and verified OM mutation evidence
- writes an audit event in the same DB transaction
- does not require `confirmed=true`, because it completes an already-dispatched generation
- no Agent direct DB write, no arbitrary DB mutation, no Ranger mutation
- no schema migration is required

Files:

- `app/services/classification_completion.py` (new)
- `app/repositories/classification_execution.py` (complete replacement)
- `app/mcp/backend_mcp_server.py` (complete replacement; R5 tools preserved + one R6-B tool)
- `tests/test_r5_mcp_registration.py` (complete replacement reflecting explicit phase extension)
- `tests/test_r6b_classification_completion.py` (new)

The Agent still needs a small, separate wiring overlay after this Backend overlay is
accepted: its production Celery task must instantiate a completion-channel adapter
instead of passing `completion=None`.
