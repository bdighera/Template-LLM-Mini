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
- When agents share one checkout, use a branch or `git worktree` per claimed task.

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
