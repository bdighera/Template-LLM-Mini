# template_llm_mini

A lightweight, standard-library Python harness for coding assistants (Claude Code,
Codex, or both side by side): **define → build → independent QC → test → memory →
done**, with a shared task board, path-scoped claims, and hooks that enforce the
rules that should never depend on a model remembering them.

Python v3.9, v3.12, v3.13 is enough for the harness. It needs no model server,
SDK, database, or third-party packages. Applications created from this template
may use third-party Python libraries.
The assistant supplies model access, delegation, and compaction.

Before writing equivalent functionality, agents evaluate existing dependencies and
suitable maintained libraries. New packages need approval of their purpose,
tradeoffs and installation scope; approved packages go in the project's virtual
environment and dependency manifest/lockfile. See the dependency policy in
[the shared rules](templates/agent_rules.md) and [workflow reference](docs/workflow.md).

## Layout

| Path | Purpose |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | Agent entry point (Codex reads AGENTS.md; CLAUDE.md imports it) |
| `templates/agent_rules.md` | Shared rules; becomes the project AGENTS.md on configure |
| `agents/<role>.md` | All role definitions (add-on roles are marked `requires:`) |
| `scripts/workflow.py` | Task lifecycle, claims, evidence, journal (all state writes) |
| `scripts/guard.py` | Mechanical rule checks used by every hook |
| `scripts/doctor.py` | Setup checks behind `workflow.py doctor` |
| `scripts/tests/` | The harness's own tests (`workflow.py self-test`) |
| `docs/workflow.md` | Command reference for configured projects |
| `tests/` | Project tests (empty in the template) |
| `.claude/settings.json` | Claude Code hooks, `WORKFLOW_AGENT=claude`, and Sonnet as the default subagent model |
| `githooks/pre-commit` | Tool-agnostic commit checks |
| `scripts/workflow.sh` | Finds a working Python for the hooks (`WORKFLOW_PYTHON` overrides) |
| `addons/agent_coordination/` | Optional localhost JSON API, scoped orchestrator assignments and MCP adapter (separate dependencies) |
| `addons/data_analysis/` | Optional fetcher and analyzer roles, approved-source fetching, inspection, profiling (tests in `tests/`) |
| `logging/` | Decision journal (`journal.jsonl` canonical, `journal.md` generated) |
| `docs/template-history/` | How the template itself was built; delete in your project if unwanted |

## Requirements

Python v3.9, v3.12, v3.13 and Git on Linux, WSL, or Windows. On Windows, install Git for Windows
(the hooks run under its `sh`) and type `python` or `py` wherever these docs say
`python3`. The hooks find a working interpreter themselves, and validation commands
that start with `python` or `python3` run with the interpreter running the harness.
`.gitattributes` keeps every file LF, which the evidence hashes depend on.

## Configure

Create the project from the template checkout, then start your assistant inside it
(Claude Code loads the hooks only from the project root):

```sh
python3 scripts/workflow.py new ~/my-project   # clone; remote "template" is fetch-only
cd ~/my-project
```

`new` keeps the template as a fetch-only `template` remote for pulling later fixes,
copies the template's repo-local Git identity, and sets `core.hooksPath`. (Without
`new`: copy the files, `git init`, and run `workflow.py install-hooks`.)

Paste [the initialization primer](prompts/template_prompt_initialization.md) with
its placeholders filled in, or ask the assistant to read AGENTS.md and configure the
harness. It runs the [interview](CONFIGURE.md), then asks for approval:

```sh
cp templates/config.json project-config.json   # fill in the agreed answers
python3 scripts/workflow.py validate-config --config project-config.json
python3 scripts/workflow.py configure --config project-config.json --approved --assumed assumed.md
python3 scripts/workflow.py doctor     # hooks, identities, template remote, fixtures
```

List unused template components in `prune` (see CONFIGURE.md, "Template
cleanup"); configure deletes them so the project carries only what it uses.

Configuring writes PROJECT.md, the project AGENTS.md, a project README.md (this
template README is replaced), `.workflow/config.json`, the task board, and
`.claude/agents/*.md` generated from the role definitions, and
sets `core.hooksPath=githooks`. It also writes a generated block at the end of
`.gitignore` that un-ignores `data.approved_fixtures` (`update-config` keeps it in
sync, `install-hooks` regenerates it, and the commit hook rejects a stale block).
The approval flag records the coordinator's assertion of consent; it cannot
authenticate the user. `validate-config` lists every structural problem at once.

## Working in a configured project

The task flow, multi-agent claims, roles, hooks, configuration updates, decision
journal, data layout, and evidence rules are in [docs/workflow.md](docs/workflow.md).

Each parallel agent that edits files must have its own Git worktree and dedicated
branch; branches alone do not isolate a shared working directory. Never switch
branches in another active agent's working directory. Read-only reviewers may
share a checkout. The coordinator integrates reviewed changes and coordinates the
authoritative task board; claims and path scopes still apply. See the workflow
reference for the limits of worktree-local state and locks.

## Validate the template

```sh
python3 scripts/workflow.py self-test
```

The harness tests use isolated temporary copies and make no provider calls.
CI runs for PRs targeting any branch, including stacked PRs, with 15-minute job
limits. Its optional coordination lane uses `scripts/ci.py coordination-status`
and `coordination-test`: a retained component must have dependencies, tests that
execute without skips, and a committed schema matching its generator. Only an
explicit workflow `prune` entry permits skipping a removed component.

## License

[MIT No Attribution](LICENSE) (MIT-0): use, copy, and modify freely, with no notice
required. Projects created from the template may choose their own license.

## North Star alignment

New tasks default to significant: new capabilities or changes to scope,
architecture, users or guiding principles. Mark only narrow fixes, maintenance
and documentation corrections `--significance routine`. Before starting a
significant feature, record an alignment assessment with `workflow.py north-star
assess --task TASK --file REPORT`, against approved NORTH_STAR.md or PROJECT.md.
Flag conflicting requests before proceeding; explicit user decisions may revise
the vision with approval and a recorded rationale.

After each ten completed tasks and each significant feature completion, synthesize
the repository into core vision, guiding principles, worthwhile capabilities,
exclusions and drift assessment with repository evidence. Python opens a pending
draft; agents supply judgment. Pending reviews block new starts and new claims;
existing owners may finish. Present the exact draft for explicit approval before
`north-star publish --approved --preview-hash HASH`, which commits the authoritative
NORTH_STAR.md and approval record. Preserve the approved vision while drafting;
unchanged assessments still need approval. See docs/workflow.md for recovery and
adoption. Never treat a pending draft as approved direction.

## Optional agent coordination

For agent discussion and orchestrator assignments, see
[the coordination component](addons/agent_coordination/README.md). It offers a
versioned HTTP/JSON API and optional MCP adapter with durable messages, separate
agent credentials, scoped local approvals and workflow-gated task starts. It is
explicitly launched, uses separately approved dependencies on Python 3.12/3.13,
and does not change the dependency-free core or launch agents/models. The coordinator
retains the authoritative task board; model findings never count as user approval.
