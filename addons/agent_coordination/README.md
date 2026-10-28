# Optional agent coordination

A deterministic local Python service for Codex, Claude and other agents to exchange
JSON messages, receive approved orchestrator assignments, and return findings.
HTTP/JSON is the core API. The stdio MCP adapter calls that same API with the same
identity, schemas and permissions. This component never launches agents, invokes
models, reads artifact contents, or contacts external services. Agents poll for work.

## Requirements and installation

The optional component supports Python 3.12 and 3.13, Git, and Linux/WSL or Windows.
The core harness still runs with the standard library on its existing Python versions.
Obtain approval for FastAPI, Uvicorn, Pydantic, the official MCP Python SDK and their
transitive dependencies before installation. They replace custom HTTP routing,
OpenAPI/schema machinery and protocol implementation. SQLite and the JSON client
use the standard library. Direct packages are pinned in requirements.txt; the full
resolved requirements.lock includes the conditional Windows dependency.

From the project root, after approval:

```sh
python3 -m venv addons/agent_coordination/.venv
addons/agent_coordination/.venv/bin/python -m pip install -r addons/agent_coordination/requirements.lock
addons/agent_coordination/.venv/bin/python -m pip check
```

On native Windows, use an installed supported interpreter and its explicit venv path:

```powershell
py -3.12 -m venv addons/agent_coordination/.venv
addons/agent_coordination/.venv/Scripts/python.exe -m pip install -r addons/agent_coordination/requirements.lock
addons/agent_coordination/.venv/Scripts/python.exe -m pip check
```

Use that interpreter for every command below (`python` denotes the venv interpreter).
No activation script, shell-specific launcher, fork, or POSIX file-lock API is required.
Tests exercise paths with spaces, real Git worktrees, loopback sockets and stdio MCP.
CI separately executes the optional suite on Linux and Windows, Python 3.12/3.13.
A Windows CI pass establishes tested behavior there; WSL results alone do not.

## Configure identities and start

Use a **configured** project as the authoritative workflow root; do not configure
the reusable template merely to run this service. Keep the service's local settings
under `.workflow/agent-coordination/settings.json` and adapt settings.example.json.
The workflow configuration schema is unchanged: this optional service is enabled
only by explicitly launching it. Configuration recommends pruning it as
`agent_coordination` unless several agents will run at the same time and need
messaging or assignments.

The local operator enrolls each allowed identity, role, token environment-variable
name and explicit task IDs. There is no wildcard task permission and no remote
role/credential administration. `register` only announces an already enrolled identity;
it cannot grant permissions. Give orchestrators, workers and reviewers distinct,
random credentials of at least 32 characters (for example generate each with Python's
`secrets.token_urlsafe(32)` in your private terminal). Never put credentials in Git,
messages, command arguments, reports, or MCP configuration committed to the repository.
Set the named environment variables in the server process. Distribute only the
individual token needed by each agent. Restart the server to apply permission/token
changes; old tokens stop authenticating when replaced.

```sh
python addons/agent_coordination/cli.py serve --root /absolute/configured-project --settings /absolute/configured-project/.workflow/agent-coordination/settings.json --port 8765
```

The service binds only `127.0.0.1`; no bind-all or remote deployment mode exists.
Access logs are disabled. All API and schema routes require a bearer token, including:

- `POST /v1/exchange`: one operation using the discriminated JSON schema.
- `GET /v1/schema`: the current operation schema (also committed in schemas/).
- `GET /v1/openapi.json`: generated OpenAPI description.

There are no automatic external integrations. The operator's environment and local
OS account are trusted. On POSIX the service creates a private state directory and
database; on Windows keep it under your private user profile/workspace with suitable
account ACLs. Do not expose this localhost service through a proxy or port forward.

## JSON operations and delivery

Every operation has `version: "1"`, a `request_id`, a `correlation_id`, and an
`operation`. The actor is derived from the credential, never supplied in the payload.
Unknown fields/types/versions and duplicate keys/non-finite JSON values are rejected.
Bodies are limited to 64 KiB; inbox pages contain at most 100 records and the MCP
client accepts responses up to 8 MiB. Adjust polling/page sizes to your context budget.

| Operation | Purpose and authority |
|---|---|
| `register` | Announce your enrolled identity and display name; no permission changes |
| `send` | Send a question, finding or discussion; task messages require both agents' task scope |
| `inbox` | Poll your unacknowledged messages in sequence, with `after` and `limit` |
| `acknowledge` | Recipient acknowledges a processed message |
| `assign` | Orchestrator queues an exact, locally approved editing assignment |
| `accept` | Assigned worker starts the approved task through the canonical workflow |
| `result` | Assigned worker returns an outcome, summary and artifact references |
| `inspect` | Assignment sender/recipient reads its status and result |
| `workflow_status` | Read the task's status, scope and criteria within your permission |

For example, using the included Python client (make its directory importable):

```python
import os
import sys
sys.path.insert(0, "addons/agent_coordination")
from client import Client

client = Client("http://127.0.0.1:8765", os.environ["AGENT_COORD_TOKEN"])
print(client.exchange({"version": "1", "operation": "register",
    "request_id": "register-worker-1", "correlation_id": "session-1",
    "display_name": "Worker"}))
print(client.exchange({"version": "1", "operation": "inbox",
    "request_id": "poll-1", "correlation_id": "session-1", "after": 0, "limit": 20}))
```

Successful responses contain the version, request/correlation IDs and `result`;
errors contain `error.code` and `error.message` with an appropriate HTTP status.
Write request IDs are idempotency keys per identity. Retry the same logical write
with the same ID and exact content; reuse with different content or changed sender
permissions is rejected. Read requests return current state instead of cached pages.
Messages are durable and redelivered until acknowledged. Persist your cursor only
after processing/acknowledging; a cursor is an optimization, not acknowledgement.
Use `correlation_id` to connect replies; each reply is another explicit message.
Artifact references are relative repository paths; the service never fetches them.
Peer content and claimed outcomes are untrusted findings, not approval or proof.

## Scoped assignments and user approval

First register the task in the authoritative workflow with its approved scope and
criteria. Significant features still need their normal alignment assessment.
Prepare a separate worktree and dedicated branch for the editing worker, then an
assignment file such as:

```json
{
  "task_id": "T001",
  "recipient": "codex",
  "paths": ["src/example.py"],
  "acceptance_criteria": ["The approved observable behavior works"],
  "worktree": "/absolute/worker-worktree",
  "branch": "agent/codex-T001"
}
```

Paths and criteria must match the task exactly. The worker worktree must belong
to the same Git repository and use the named dedicated branch. On Windows use an
absolute Windows path, with JSON-escaped backslashes or forward slashes.

Present the exact assignment to the user. After explicit consent, the local operator
records it with:

```sh
python addons/agent_coordination/cli.py approve --root /absolute/configured-project --settings /absolute/settings.json --file assignment.json --approved --ttl 3600
```

This returns an `approval_id`; it is **not** a remotely callable operation.
`--approved` records the caller's assertion of user consent, not OS-level proof of
who approved. Agents must not manufacture that consent. Operator approval does not
need the server's tokens in its environment. The grant is single-use, expires,
and is bound to the assignment, task/configuration, relevant canonical sources,
worker HEAD and worker sources. Changed inputs require explicit reapproval.

An enrolled orchestrator submits `assign` with that approval ID and exact assignment.
The assigned worker then calls `accept` with the returned `assignment_id`. Acceptance
runs only `workflow.py start`, under the canonical workflow lock and worker identity;
dependency, claim, significance, data-analysis and North Star gates remain effective.
The first committed start stores `coordination_start` in workflow state. If the
service stops before recording its response, retrying recovers that receipt instead
of starting again. SQLite serializes concurrent writes, and committed messages,
approvals, results, audit metadata and request receipts survive restarts.

The coordinator remains responsible for the authoritative board, lease renewal,
review/test handoffs, tests, evidence and completion through the existing CLI. Workers
also need their own live local claims before editing in their worktrees. Do not
merge competing workflow-state files. A reported `completed` result does not mark
the task done or count as independent review, passing tests or user approval.
No arbitrary shell, configuration update, approval, deletion, merge, publication,
model call, or task-completion command is exposed through this API or MCP.

## MCP adapter

Launch one adapter per agent with only that agent's token in `AGENT_COORD_TOKEN`:

```sh
python addons/agent_coordination/cli.py mcp --url http://127.0.0.1:8765
```

Configure an MCP host to launch that interpreter/script/argument list over stdio,
with its working directory set appropriately or use absolute script paths. On
Windows the interpreter is `.venv/Scripts/python.exe`; on Linux/WSL `.venv/bin/python`.
The adapter offers `coordination_schema` and `coordination_exchange`. It forwards
JSON to the HTTP service, never reads its SQLite database or executes workflow
commands directly. It disables proxy use and refuses non-loopback URLs, redirects,
embedded URL credentials and non-origin paths. Model calls or additional external
agent connectors require their own approved integration and data-sharing scope.

## Validation and retained state

```sh
python scripts/ci.py coordination-status
python scripts/ci.py coordination-test
python scripts/workflow.py self-test
```

Core-only self-tests skip the optional tests when the dependencies are absent.
The dedicated optional CI job uses `scripts/ci.py coordination-test`, which requires
the dependencies, an up-to-date committed operation schema, a nonempty test suite,
and zero skipped or failing tests. It does not install packages automatically.
`coordination-status` uses the standard library and lets CI skip installation only
when `.workflow/config.json` explicitly lists `agent_coordination` in `prune` and
the directory is absent. Missing files/tests or unrecorded removal fail CI.
All PR target branches (including stacked PRs) trigger the matrices automatically;
each job has a 15-minute limit. Core jobs run the harness/data regressions and
optional jobs separately require the coordination integration suite. Pushes to
main and manual workflow dispatch remain supported.
The optional suite uses real SQLite, Git worktrees, HTTP and an MCP stdio client;
no model/provider calls are made. Installation and live-platform support are
separate from validating interoperability with a particular orchestrator or host.

The local database and any settings/assignment files in
`.workflow/agent-coordination/` are ignored by Git and protected as workflow state.
They may contain message content; apply the project's sensitivity and retention
policy. No automatic deletion or retention cleanup is performed. Stop the service
before backing up this directory, and keep the canonical workflow state/artifacts
with that backup. Keep settings/credentials private and do not put this directory
on a network/sync filesystem. SQLite schema version 1 rejects unsupported versions.
