# template_llm_mini

A lightweight Python workflow harness for coding assistants such as Claude Code
and Codex: **define → build → independent QC → test → memory → done**.
It combines approved project configuration, a task board, scoped claims, evidence
checks and concise handoffs. The assistant supplies model access, delegation and
context compaction; the harness records and checks the workflow.

The **core uses only Python's standard library and Git**. The optional local
coordination service adds an HTTP/JSON API and MCP adapter with separately approved
dependencies. Applications created from the template may use third-party libraries.

## Current features

| Area | What is included |
|---|---|
| Project setup | Clone into a new project, complete a configuration interview, run read-only preflight, and apply the explicitly approved configuration. Prune unused template components and check setup with `doctor`. |
| Task management | A generated task board, acceptance criteria, dependencies, linked follow-up tasks, statuses, blocking/unblocking, and repair limits. Completed tasks retain their history; later changes use follow-ups. |
| Agent coordination | Named identities, path-scoped claims, expiring leases, heartbeat/release, overlap checks and a configurable limit on agents holding live claims in one workflow copy. Parallel editors use separate worktrees and branches. |
| Review and completion | Builder/reviewer/tester handoffs, current `review_ref` binding, executed test results, feature memory and a final coordinator handoff. Artifact hashes and source fingerprints detect changed or stale evidence. |
| Roles and integrations | Required builder/reviewer/tester definitions, custom roles, runtime/tool/write policies, and generated Claude subagents. Optional fetcher/analyzer roles load with the data-analysis add-on. |
| Context and continuity | Focused role context packets, keyword-ranked feature-memory recall, checkpoint/resume records, stale-checkpoint warnings and configured context limits. |
| Decision history | An append-only JSONL journal with a generated Markdown view, compact queries, linked evidence, superseding decisions and automatic workflow milestones. |
| Guards and setup checks | Claude hooks, a Git pre-commit hook, and a shared guard CLI for protected workflow files, raw data, source claims, download approval, secret-like content and commit checks. |
| Optional data analysis | Approved-source fetch/parse scripts, dataset inspection, column rules, pre/post analysis gates, provenance/version tracking, user clarification and approved aggregate profiling. |
| Optional JSON/MCP service | Authenticated local messaging, acknowledgements, scoped orchestrator assignments, explicit local approval grants, durable results and retry recovery through the canonical workflow. |
| Validation and CI | Configured project checks plus harness regressions. Linux/Windows CI, stacked-PR triggers, strict optional-suite execution, schema freshness, recorded-pruning detection and job timeouts. |

## Requirements

| Component | Environment |
|---|---|
| Core harness and data-analysis add-on | Python and Git; CI covers Python 3.9, 3.12 and 3.13 on Linux and native Windows. Local development also uses WSL. No third-party packages are required for these shipped components. |
| Optional coordination service and MCP adapter | Python 3.12 or 3.13, Git, a dedicated virtual environment, and the pinned dependencies in [its manifests](addons/agent_coordination/requirements.txt). Linux and native Windows have dedicated CI jobs. |
| Coding assistant | An existing assistant session provides model access and performs interpretation, building and independent review. The template contains no standalone model runner. |

On Windows, install Git for Windows: the hooks use its `sh`. Use `python` or
`py -3.12` in place of `python3` as appropriate. The hook launcher finds a working
interpreter (`WORKFLOW_PYTHON` overrides it). Validation commands starting with
`python` or `python3` use the interpreter running the workflow. `.gitattributes`
keeps tracked files LF, including on Windows.

Before implementing equivalent functionality, agents inspect existing dependencies
and consider suitable maintained libraries. New packages require approval of their
purpose, tradeoffs and installation scope, then installation in the project's
virtual environment with manifest/lockfile updates. Include dependency files in
validation source paths. See [the shared rules](templates/agent_rules.md).

## Start a project from the template

Configure a **copy**, not this template checkout. These examples use a POSIX shell;
on native Windows use the corresponding paths and interpreter commands.

```sh
python3 scripts/workflow.py new ~/my-project
cd ~/my-project
```

`new` clones committed files, creates a fetch-only `template` remote, copies any
repo-local Git identity, and sets `core.hooksPath=githooks`. Uncommitted template
changes are not copied. Add your own project remote when ready to publish.

Start the assistant in the new project's root. Ask it to read `AGENTS.md` and run
the complete [configuration interview](CONFIGURE.md), or use
[the initialization primer](prompts/template_prompt_initialization.md). The primer
is a prompt template; displaying it does not execute it.

The interview covers purpose/scope, technology, data/evidence, add-ons, roles,
parallel agents, review policy, tasks/handoffs, prompts/memory, decisions, context,
validation, approvals, delivery and cleanup. The assistant presents the completed
brief, configuration, changes and validation plan for explicit approval.

Once those answers are recorded in a candidate configuration:

```sh
cp templates/config.json project-config.json   # fill with the agreed answers
python3 scripts/workflow.py validate-config --config project-config.json
# Only after explicit user approval:
python3 scripts/workflow.py configure --config project-config.json --approved
python3 scripts/workflow.py doctor
```

If approval accepts stated open-item defaults, put that list in a real file and
pass it with `--assumed assumed.md`. Configuration writes `PROJECT.md`, project
instructions, `.workflow/config.json`, the board and generated Claude agents.
It replaces the template README with a project README; an adopted non-template
README is preserved. It also maintains the approved-fixture `.gitignore` block.

Recommend pruning unused data-analysis/coordination components, template history,
the primer/interview, and Claude integration as appropriate. Configuration applies
the agreed removals. Subsequent configuration changes use the approved
`update-config` preview/hash process, preserving task history.

## Work with tasks and agents

Use [the workflow reference](docs/workflow.md) for complete command syntax.
Set `WORKFLOW_AGENT` or pass `--agent`; Claude's shipped settings use `claude`.
Codex and other tools can use `--agent codex`. Unset identity is recorded as `local`
and produces warnings for workflow mutations.

Register an approved task with explicit paths and acceptance criteria, start/claim
it, then follow the role handoffs. Completion requires current review, passing
executed checks, a feature memory and a final coordinator handoff. Failed checks
invalidate prior passes; source changes require fresh evidence. The configurable
repair limit stops repeated repair cycles for user direction.

Useful read-only entry points after configuration:

```sh
python3 scripts/workflow.py status --agent codex
python3 scripts/workflow.py context T001 --role builder --agent codex
python3 scripts/workflow.py recall 'relevant feature'
python3 scripts/workflow.py journal --task T001
```

Independent review, cross-agent review, and an explicitly approved same-agent
fallback are supported review policies. Workers return reports and proposed
handoffs; the coordinator records authoritative transitions. Roles live in
[agents/](agents/), with editable handoff/memory formats in [templates/](templates/).
`sync-agents` regenerates Claude role agents. The shipped Claude settings specify
Sonnet as the default for subagents without their own model; role model hints are
mapped separately by [the role generator](scripts/roles.py).

Each parallel editing agent must have its own Git worktree and dedicated branch.
Never switch branches in another active agent's directory. Read-only reviewers may
share a checkout. Workflow locks and claims are **local to each worktree**, not
global: the coordinator maintains the authoritative board, workers hold their own
local claims covering assigned paths, and reviewed source changes are integrated
without merging competing workflow-state copies.

## Evidence, guards and decisions

`.workflow/state.json` is authoritative; `tasks.md` and memory indexes are generated.
Use the workflow CLI for its state/artifacts instead of hand-editing them. Source
fingerprints cover configured source paths plus referenced task source files;
choose those paths to include application code, tests and dependency manifests.

Claude's hooks load when its session starts in the project root. Other tools can
invoke `workflow.py guard edit|bash|session|commit`; see the command reference.
The pre-commit hook checks journal rewrites, data files, secret-like content and
fixture-ignore consistency. `doctor` reports setup issues such as hooks, identities, template
remotes and missing fixtures. Guards are mechanical checks, not an OS sandbox;
shell checks are best effort and shell source edits are not claim-checked.

External evidence belongs under `data/`: originals in `raw/`, transformations in
`processed/`, synthetic/model outputs in `generated/`. Originals are read-only;
Git ignores data contents except explicitly approved fixtures. Downloads require
approval (registered data sources can provide that approval). Preserve attribution
and uncertainty rather than inventing missing facts. New package installation is
approved environment setup, distinct from fetching external evidence.

Record significant decisions and milestones with `decide`. The canonical journal
is append-only; corrections supersede earlier entries. Queries are compact by
default, and the Markdown view is regenerated. If an append succeeds but rendering
fails, repair the view without appending the decision again. See
[the journal guide](logging/README.md).

## Optional components

**Data analysis** is enabled by approved configuration with
`addons.data_analysis: true` and an `analysis` section. It adds fetcher/analyzer
roles, approved fetch/parse runs, bounded inspection of UTF-8 CSV/JSON/JSONL/text/
Markdown, required-column/value rules, pre/post analysis, input-version/provenance
tracking and clarification gates. Other formats need approved Python extraction.
Profiling is off by default; when approved, it provides bounded aggregates without
sample rows or category values. Aggregates can still be sensitive. These checks
validate structure and recorded references, not factual truth or production lineage.
See [the data-analysis guide](addons/data_analysis/README.md).

**Agent coordination** is enabled by explicitly installing its approved dependencies
and launching the local service; it does not add a workflow configuration flag.
Its versioned HTTP/JSON API uses separate enrolled credentials, roles and explicit
task scopes. It supports registration announcements, peer questions/findings,
polling, acknowledgements, exact locally approved assignments, acceptance, results
and status. SQLite persists messages, grants, audit metadata and idempotency receipts.

The optional stdio MCP adapter uses that same HTTP authorization and schemas.
Assignment approval binds task/scope, canonical sources and the worker worktree/
branch/HEAD/source. Durable workflow start receipts recover interrupted responses
without starting twice. Results are findings, not user approval or workflow
completion. The coordinator still handles leases, review, tests and completion.
The service binds only to localhost and makes no model calls or automatic agent
launches. External-orchestrator interoperability requires separate validation.
See [setup and Windows commands](addons/agent_coordination/README.md).

## Validation and CI

```sh
# Core and retained add-on regressions; no provider calls:
python3 scripts/workflow.py self-test
# Dependency-free optional-component presence/pruning check:
python3 scripts/ci.py coordination-status
# With the approved optional environment installed (Linux/WSL):
addons/agent_coordination/.venv/bin/python scripts/ci.py coordination-test
```

On Windows, the strict command uses
`addons/agent_coordination/.venv/Scripts/python.exe`. Project checks are configured
in `validation.commands` and recorded with `workflow.py test TASK`; harness tests
live separately from the project's `tests/` folder. Validation has configurable
command timeouts and optional bounded-summary diagnostics.

CI automatically runs for PRs to any branch (including stacked PRs), pushes to
`main`, and manual dispatch. Core Linux/Windows jobs cover Python 3.9/3.12/3.13;
strict coordination jobs cover 3.12/3.13. Every job has a 15-minute limit.

Core-only runs intentionally skip coordination tests when dependencies are absent.
The dedicated optional lane requires installed dependencies, a nonempty suite
without skips/failures, and a committed JSON schema matching the generator. Its
installation/tests are omitted only when approved workflow configuration records
the removed component in `prune`; missing or partially removed components fail.
Tests exercise real SQLite, Git worktrees, loopback HTTP and MCP stdio, without
calling models. Python verifies workflow structure and evidence freshness;
actual user consent, genuine reviewer independence and semantic accuracy remain
human/agent responsibilities. The harness provides no OS containment guarantee.

## Repository map

| Path | Purpose |
|---|---|
| `AGENTS.md`, `CLAUDE.md`, `templates/agent_rules.md` | Template entry points and shared rules; configuration creates project instructions |
| `CONFIGURE.md`, `templates/config.json`, `prompts/template_prompt_initialization.md` | Interview, candidate configuration and initialization primer |
| `scripts/workflow.py`, `scripts/coordination.py` | Task lifecycle, claims, local state locking, handoffs and evidence |
| `scripts/decision_journal.py` | Append-only decision history |
| `scripts/guard.py`, `scripts/doctor.py`, `scripts/workflow.sh`, `githooks/` | Shared guards, setup diagnostics and hook launcher |
| `agents/`, `scripts/roles.py`, `.claude/` | Role policies and Claude integration/generation |
| `scripts/ci.py`, `scripts/tests/`, `.github/workflows/ci.yml` | Strict CI tooling, harness regressions and platform matrices |
| `addons/data_analysis/`, `addons/agent_coordination/` | Optional data workflow and local JSON/MCP service |
| `tests/` | Application tests; empty in the template |
| `data/` | Raw, processed and generated data, ignored by default |
| `logging/`, `memories/`, `handoffs/`, `prompts/logs/` | Workflow decision, context and evidence artifacts |
| `docs/workflow.md`, `docs/template-history/` | Command reference and historical template-maintenance records |

## License

[MIT No Attribution](LICENSE) (MIT-0). Projects created from the template may choose
their own license.
