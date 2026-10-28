# Optional agent coordination API and MCP adapter

Approved scope: local versioned HTTP/JSON API, SQLite persistence, registration of
pre-enrolled identities, messages/acknowledgements, scoped assignments, results and
an optional MCP adapter. Polling only; no model calls, agent launching or external
transmission. User separately approved FastAPI, Uvicorn, Pydantic and the official
MCP SDK with transitive dependencies in a dedicated optional virtual environment.
User also requested native Windows support after the initial WSL implementation.

Implemented strict Pydantic contracts and generated JSON schema/OpenAPI, per-agent
credentials/roles/task permissions, single-use expiring local approval grants bound
to the exact assignment, canonical task/config/source and worker branch/HEAD/source,
and a shared HTTP/MCP authorization path. SQLite transactions retain messages,
acknowledgements, results, audit metadata and principal-bound idempotency receipts.
An atomic workflow coordination_start receipt prevents duplicate task starts after
an interrupted service response. Existing workflow gates still apply; result reports
never mark tasks done or replace user approval, independent QC, tests or evidence.

Independent coordination_security_qc passed after fixing permission replay, inbox
pagination following scope changes, fixture virtualenv copying, worker-source binding,
current-role acceptance and large inbox response bounds. It executed all 15 optional
tests and checked schema generation and diff whitespace. Full WSL verification:
98 core +40 data add-on +15 coordination =153 passing tests; pip check passed.
Core-only tests passed without optional dependencies (coordination tests skipped).

Hosted CI run 40672429285 at implementation commit
cce94fe8f59d4f91e8821f94e59e52aec1453ff2 passed all ten jobs:
six core jobs on Linux/Windows Python 3.9/3.12/3.13, plus four fully installed
coordination jobs on Linux/Windows Python 3.12/3.13. Native Windows exercised real
SQLite, worktrees and paths with spaces, loopback HTTP, and MCP stdio. This verifies
hosted Windows behavior, not installation on the user's own Windows machine.
Run: https://github.com/bdighera/Template-LLM-Mini/actions/runs/40672429285

Post-feature alignment assessment: the component supports the template's approved
agent collaboration purpose, keeps deterministic logic in Python and core dependencies
unchanged, and preserves explicit approval and human coordinator authority. No scope
expansion into model running, remote access or automatic completion is justified.
The significant-feature completion hook will request the next vision review in the
isolated maintenance tracker; no authoritative NORTH_STAR.md is published by this task.

The template stays unconfigured. Task T005 is tracked in an isolated configured copy.
PR #9 is stacked on still-open PR #8 to avoid mixing the earlier isolation-policy
change. The final follow-up commit adds only archived decision/review/test evidence;
CI above validates the implementation commit. External orchestrator/client-host
interoperability remains untested, and explicit user consent is recorded as a local
operator assertion, not cryptographically authenticated human approval.
