# Workflow reference

Command reference for a configured project. Agents read AGENTS.md first; this file
explains the commands it names. Run every command from the project root.

## Setup checks

```sh
python3 scripts/workflow.py doctor          # hooks, identities, template remote, fixtures
python3 scripts/workflow.py install-hooks   # core.hooksPath + approved-fixture .gitignore block
python3 scripts/workflow.py self-test       # the harness's own tests (after changing scripts/)
```

`doctor` exits 2 when it has warnings. The SessionStart hook repeats its warnings.

## Dependencies and reuse

The core harness uses only the Python standard library. Projects created from it
may use third-party Python libraries. Inspect existing dependencies and evaluate
suitable maintained libraries before implementing equivalent functionality; explain
when custom code is the better fit.

Before installing new packages, propose their purpose, tradeoffs and installation
scope for explicit approval, either during configuration or in the task plan.
Use the project's virtual environment and existing package manager; record direct
dependencies and supported versions in its manifest and update lockfiles where
used. Include these files in task paths and `validation.source_paths` so changes
invalidate stale review/test evidence. Run import checks and relevant regression
tests with the environment's interpreter, including `workflow.py test TASK` when
recording formal results. Do not introduce an automatic installer or third-party
dependencies into the core harness without separate approval.

Package installs are approved environment setup, not datasets requiring staging
under `data/raw/`. Downloads of external evidence still follow the data policy.
Dependency selection, reuse and installation approval are agent policy; the
workflow has no package resolver or automatic installation mechanism.

## Agents side by side

Each agent sets an identity: Claude Code gets `WORKFLOW_AGENT=claude` from
`.claude/settings.json`. For Codex, export `WORKFLOW_AGENT=codex` in its
environment (or pass `--agent codex`). Unset means `local`, and mutating commands
warn about it; `doctor` reports it too.

```sh
python3 scripts/workflow.py add --title 'API endpoint' --criterion 'GET /items works' \
    --path 'src/api/**' --path 'tests/test_api.py' --prompt request.md
python3 scripts/workflow.py start T001        # claims T001 for this agent (lease)
python3 scripts/workflow.py status            # board as JSON; tasks.md is the same board
python3 scripts/workflow.py claim T002 --ttl 60
python3 scripts/workflow.py heartbeat T001    # extend the lease
python3 scripts/workflow.py scope T001 --path 'src/api/**' --path 'src/models.py'
python3 scripts/workflow.py release T002
```

- Every mutation takes a file lock (`.workflow/lock`), so concurrent agents cannot
  corrupt state. Waiting is capped by `WORKFLOW_LOCK_TIMEOUT` (default 300 s).
- A claim stops other agents from changing that task or claiming a task whose
  paths overlap it. Overlap is conservative: `src/**` overlaps `src/api/x.py`.
- `delegation.max_concurrency` caps how many agents hold claims at once.
- Expired leases can be taken over; `done` releases the claim.
- `review_mode: cross-agent` requires a different agent to record the review.
- Agent identity is stored on handoffs, reviews, tests, and journal entries.
- Each parallel agent that edits repository files MUST use its own Git worktree
  and dedicated branch. A branch alone does not isolate a shared working directory.
  Never switch branches in another active agent's working directory. Read-only
  reviewers may share a checkout. The coordinator integrates reviewed changes;
  task claims and path scopes still apply.

Agree worktree paths, dedicated branches and the authoritative task board before
parallel editing begins. Git worktrees have separate working files, including
their own copies of `.workflow/`; workflow locks and claims are local to each copy,
not global across worktrees. The coordinator keeps the authoritative board and
coordinates assignments and path scopes across workers. Workers return reports
and proposed handoffs; the coordinator records workflow transitions in that
authoritative checkout and integrates reviewed source changes. Do not merge
competing copies of workflow state or treat local claims as cross-worktree locks.
Before editing, each worker must also hold a live claim under its own identity in
its local workflow copy, covering the coordinator-assigned paths. Arrange that
local claim through the workflow CLI; a coordinator's claim in another worktree
does not satisfy the local guard. Keep local assignments consistent with the
authoritative board and return reports rather than overwriting its state.

## Task flow

```sh
python3 scripts/workflow.py context T001 --role builder
python3 scripts/workflow.py handoff T001 --to reviewer --file handoff.json
python3 scripts/workflow.py context T001 --role reviewer     # includes review_ref
python3 scripts/workflow.py review T001 --verdict pass --file review.md --reviewer claude-reviewer
python3 scripts/workflow.py handoff T001 --to tester --set next_action='Run checks'
python3 scripts/workflow.py test T001
python3 scripts/workflow.py memory T001 --file feature-memory.md
python3 scripts/workflow.py handoff T001 --to coordinator     # attaches the latest memory
python3 scripts/workflow.py recall 'parser cache'            # past memories ranked by keyword match
python3 scripts/workflow.py done T001
python3 scripts/workflow.py check
```

Use templates/handoff.json for the first handoff and templates/memory.md. Later
handoffs may omit `--file`: they reuse the previous handoff's fields, with
`--set FIELD=VALUE` overrides (`files`/`memories` take comma-separated paths). Small
requests can use `add --prompt-text '...'` instead of `--prompt FILE`.

The review report must contain the current `review_ref` from the reviewer context;
it changes with every build and repair cycle, so a report written against an older
version is rejected. In `independent` and `cross-agent` modes the reviewer must
differ from the builder: pass `--reviewer NAME` when the coordinator records a
subagent's review. Like `--approved`, the name is an assertion that Python records
but cannot authenticate.

A failed review or test invalidates earlier passes and returns the task to building;
exceeding `handoffs.max_repairs` blocks it for user direction. `block`/`unblock`,
`rebuild`, `log` (scope changes), and `--depends` cover the rest. Completed tasks are
immutable; changes need a follow-up task. A review can pass with deferred findings:
register them with `add ... --follows T001` (linked, not blocking); `done T001`
lists open follow-ups.

Review and tests are bound to a fingerprint of `validation.source_paths` plus the
handoff's files, so later edits invalidate them. Include application source, tests,
and dependency manifests in `source_paths`. Hashes detect drift, not truth.

Validation commands are argument arrays run without a shell from the project root,
with `timeout_seconds` (1–3600). Output is discarded unless `diagnostics=summary`,
which accepts only a bounded JSON `{"summary": "..."}` from an approved wrapper.
Exit code 2 means failed checks or a pending gate; 1 means a workflow error.

## Roles

`config.roles` lists the roles (builder, reviewer, and tester are required; add
your own). Every role, including add-on roles, has `agents/<role>.md`. A role with
`requires: data_analysis` (analyzer, fetcher; absent once the add-on is pruned)
loads automatically when that add-on is enabled and is ignored otherwise; do not
list it in `config.roles`.

```yaml
---
name: reviewer
description: Independently reviews a task's diff; read-only.
runtime: any            # any | claude | codex
model_hint: strong      # inherit | fast | balanced | strong
writes: []              # globs, or [task] for the claimed task's paths
tools: [read]           # read | edit | shell | web
independent_of: builder
requires: none          # or an add-on name, e.g. data_analysis
---
Short role-specific prompt.
```

`context --role` includes the policy. `sync-agents` regenerates the Claude
subagents in `.claude/agents/` (skipping `runtime: codex`). Codex is assigned a role
by pointing it at `agents/<role>.md`. The guard enforces `writes` when it knows the
role: from the Claude hook's `agent_type`, from `--role`, or from `WORKFLOW_ROLE`.

Claude subagents with no model of their own, such as the built-in `general-purpose`,
`Explore`, and `Plan`, run on Sonnet through `CLAUDE_CODE_SUBAGENT_MODEL` in
`.claude/settings.json`. Roles keep the model their `model_hint` maps to. To change
the default, edit or remove that setting.

## Hooks

| Rule | Where it is enforced |
|---|---|
| Workflow-owned files are never hand-edited | Claude `PreToolUse` (Edit/Write/Bash) |
| `data/raw/` originals are read-only; new raw files need approval | Claude `PreToolUse` |
| Source edits need your own live claim covering the path | Claude `PreToolUse` (edit tools) |
| Downloads (`curl`, `wget`, WebFetch, WebSearch) need approval | `PreToolUse` ask + permissions |
| Secret-like content needs approval or redaction | `PreToolUse` ask; pre-commit block |
| No commit-hook bypass | `PreToolUse` (Bash) |
| Journal only grows; no data contents or `.env` in Git | `githooks/pre-commit` |
| Board, claims, and resume packet after compaction | Claude `SessionStart` |

Claude Code loads `.claude/settings.json` (the hooks and `WORKFLOW_AGENT=claude`)
only when the session starts in the project root. In a session rooted elsewhere,
nothing fires: export `WORKFLOW_AGENT`, and call `workflow.py guard edit|bash`
yourself before writes.

Other tools can call the same checks: `workflow.py guard edit --path P`,
`guard bash --command C`, `guard session`, or `guard hook` with Claude-style JSON
on stdin (exit 2 means deny or ask). If your Codex version supports hooks, point
them at these commands; otherwise the pre-commit hook and the locked CLI still
apply. Shell checks are best-effort pattern matching, not a sandbox. Bash source
edits are not claim-checked.

## Configuration updates

```sh
python3 scripts/workflow.py update-config --config candidate.json
python3 scripts/workflow.py update-config --config candidate.json --approved --preview-hash <hash>
```

The preview shows changed fields, guidance diffs, affected tasks, and conflicts.
The hash binds the candidate, current config/state, and guidance, so a stale preview
must be redone. Revisions in `.workflow/config-revisions/` keep the old and new
configuration and prior state. Applying invalidates active evidence and keeps
completed history. Config version 2 is not compatible with version 1 projects;
migrate those by building a fresh candidate from templates/config.json.

## Decision journal

```sh
python3 scripts/workflow.py decide --task T001 --file decision.json
python3 scripts/workflow.py journal --task T001
python3 scripts/workflow.py journal --id J000004 --full
python3 scripts/workflow.py decide --file revised.json --supersedes J000004
```

Configuration, task creation and completion, and config updates add milestones
automatically; agents record significant decisions explicitly. Queries are compact
by default (latest 20, 6000 characters). See [logging/README.md](../logging/README.md).

## Data

`data/raw/` holds read-only originals, `processed/` transformations, and
`generated/` synthetic data. Git ignores contents except `.gitkeep` and
`data.approved_fixtures`. The optional data-analysis add-on (analyzer, fetcher,
dataset contracts, provenance, profiling) is documented in
`addons/data_analysis/README.md` when the project keeps it; enable it with
`addons.data_analysis: true` plus an `analysis` section copied from its `config.json`.

## State and evidence

`.workflow/state.json` is authoritative; `tasks.md` and `memories/index.md` are
generated. Keep state, artifacts, and config revisions together in Git. Multi-file
updates are not one transaction.

## Validate

Project checks are `validation.commands`, run by `workflow.py test TASK`. The
harness's own tests live in `scripts/tests/` (plus `addons/data_analysis/tests/`
when kept); run them with `workflow.py self-test` when harness files change. They
use isolated temporary copies, make no provider calls, and skip add-on cases once
the add-on is pruned.

## North Star checkpoints

The Python task-completion hook requests a review at 10, 20, 30… completed tasks,
and on completion of every significant feature. An overdue adopted repository
gets one current-state review; coincident triggers share an assessment. Pending
reviews block `start` and new `claim` operations. Existing owners may renew leases
and finish their work. `status`, task context and session hooks surface the gate.
The first checkpoint creates `.workflow/NORTH_STAR.draft.md`, explicitly unapproved.
Python never generates the qualitative synthesis or launches a model.

Task registration defaults to `--significance significant`. New capabilities or
changes to scope, architecture, users or principles are significant. Use
`--significance routine` for narrow fixes, maintenance and documentation corrections.
Before implementing a significant task, assess alignment with the authoritative
North Star (or PROJECT.md before the first publication):

```sh
python3 scripts/workflow.py north-star assess --task T001 --file alignment.md
python3 scripts/workflow.py north-star status
python3 scripts/workflow.py north-star draft --file proposed-vision.md
python3 scripts/workflow.py north-star publish --approved --preview-hash <hash>
```

The agent summarizes current repository evidence, approved scope, implementation,
completed work and decisions into `## Core vision`, `## Guiding principles`,
`## Worth building`, `## Exclusions`, `## Drift assessment` and
`## Repository evidence` sections. Cite repository paths; distinguish observed
behavior, aspirations, inference and unknowns. Assess what is valuable, rather
than treating every existing capability as a reason to keep building it.
A draft may be unchanged in substance: explain why alignment still holds.

Present the exact draft and assessment to the user. Only explicit approval of
that preview authorizes `publish`. Its hash binds the draft, configured source
selection, configuration and current task state; changing those requires a fresh
preview and approval. Approval covers `validation.source_paths` plus Git-tracked and unignored source files,
including repository documentation; data and workflow artifacts are excluded.
Publication commits only root `NORTH_STAR.md` and its `.workflow/north-star-approval.json` and `.workflow/north-star.json` records,
runs normal Git hooks, and excludes unrelated staged changes. Commit failure
keeps the gate pending; retry with the same approval only if its inputs remain
current. Never bypass a failing hook. Committed `.workflow/north-star.json` tracks
recoverable publication state; keep it with the workflow artifacts in Git.

The committed North Star becomes authoritative for planning. Flag conflicting
requests before proceeding. Explicit user decisions may revise it through the same
approval process, with rationale recorded in the decision journal. Significant
feature completion always triggers reassessment; the ten-task cadence remains a
separate backstop. An unapproved, modified or first-discovered NORTH_STAR.md also
requires review; an existing approved version remains in place while drafting.

## Optional JSON API and MCP coordination

addons/agent_coordination/README.md documents the explicitly launched local service.
Both transports use one versioned JSON contract, sender identity and permission
logic; no model calls or agent activation are performed. The core remains standard
library only. The optional component requires separately approved dependencies.

Local operator approval binds a planned assignment to its task scope/criteria,
worker worktree/branch and source snapshot. Orchestrator messages cannot provide
user consent. Acceptance runs the canonical workflow start under its existing lock
and gates; `start --operation-id ID` records a durable `coordination_start` receipt
with the same state write, permitting recovery after an interrupted API response.
Repeating that start ID under the same agent returns without another mutation.
Normal CLI starts remain unchanged. Results are findings; the coordinator still
records review, tests, memory, lease management and completion through this CLI.
Workers must arrange their own local claims before editing isolated worktrees.

Service credentials are environment variables referenced by private settings;
messages, grants and request receipts stay in ignored local SQLite state. Do not
hand-edit the database, expose it remotely, or commit credentials/message contents.
The service has no remote approval, publication, configuration update or completion
operation. Optional tests run via self-test when dependencies exist; core-only runs
skip them. See the component README for Windows setup and hosted validation.
