# template_llm_mini

A lightweight, standard-library Python harness for coding assistants (Claude Code,
Codex, or both side by side): **define → build → independent QC → test → memory →
done**, with a shared task board, path-scoped claims, and hooks that enforce the
rules that should never depend on a model remembering them.

Python v3.9, v3.12, v3.13 is enough. No model server, SDK, database, or third-party packages.
The assistant supplies model access, delegation, and compaction.

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

## Validate the template

```sh
python3 scripts/workflow.py self-test
```

The harness tests use isolated temporary copies and make no provider calls.

## License

[MIT No Attribution](LICENSE) (MIT-0): use, copy, and modify freely, with no notice
required. Projects created from the template may choose their own license.
