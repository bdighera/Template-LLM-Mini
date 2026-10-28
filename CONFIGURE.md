# Configuration interview

Present ALL topics at the start. Explain each choice and its fixed constraints,
prefill what is already known, and offer the recommended defaults for batch
acceptance. Resolve missing, conflicting, or ambiguous answers before
implementation. Record the answers in a copy of templates/config.json. Every field
is required; use `none` or `not applicable` explicitly. Then show the brief,
configuration, proposed changes, and validation plan for explicit approval before
`configure --config project-config.json --approved`.

End the approval request with an **"Assumed unless you object"** list naming every
item still open and the default that will apply. A bare "approved" then accepts
exactly those defaults; an empty list means nothing is assumed. Pass the list with
`configure ... --assumed assumed.md` so the configuration milestone in the journal
records it. Afterwards, `workflow.py doctor` checks hooks, identities, the
template remote, and approved fixtures.

| Topic | Ask | Default |
|---|---|---|
| Project | Name, purpose, users, scope, exclusions, observable acceptance criteria? | One focused deliverable |
| Technology | Languages, frameworks, dependencies, environment? | Python for all deterministic components |
| Data | Sources, sensitivity, size, retention, generated outputs, approved Git fixtures? | raw/ read-only, processed/, generated/; contents ignored; `approved_fixtures: []` (configure un-ignores listed fixtures) |
| Evidence | External data staged under ./data, download approvals, unknown-source handling? | ./data only; explicit download approval; "source unavailable" rather than invention |
| Data-analysis add-on | Will tasks fetch, consume, or produce datasets that need inspection or an analyzer? | `addons.data_analysis: false`; if true, add the `analysis` section (below) |
| Roles | Builder/reviewer/tester policies; any custom roles; which runtime plays each? | Three required roles in `agents/`; analyzer and fetcher load with the data add-on |
| Agents | Which assistants work in parallel (Claude, Codex, ...)? Lease length? | `max_concurrency: 2` agents; `lease_minutes: 120` |
| Review | Independent review, approved same-agent fallback, or cross-agent? Reviewer identity? | `independent` (reviewer named with `--reviewer`); `cross-agent` when two assistants are available |
| Handoffs | Repair allowance? | Built-in fields; `max_repairs: 2` |
| Tasks | Granularity, dependencies, path scopes, done criteria? | One reviewable feature per task with explicit `--path` globs |
| Prompts and memory | What counts as a major prompt; retention? | Requests, scope changes, assignments; one feature memory per task |
| Decision journal | Which choices are significant; retrieval budget; exclusions? | Significant choices and milestones; compact latest 20 / 6000 chars; no secrets or raw data |
| Context | Context size and compaction expectations? | `context.max_chars: 12000`; checkpoint before compaction |
| Validation | Source roots, test commands, timeout, diagnostics? | Add application dirs to `source_paths`; `unittest discover -s tests` (fails until the project has a test); `timeout_seconds: 60`; diagnostics off |
| Approvals | What needs approval beyond the defaults? | Every feature enhancement, substantial fixes, downloads, setup |
| Delivery | Documentation, deployment, supported environments, definition of done? | Evidence, review, executed tests, memory |
| Execution boundary | Any component where deterministic vs judgment is unclear? | Clarify first and record in `boundary_decisions` |
| Template cleanup | Which template components does this project not need? | Prune everything the use case does not need (see below) |

## Template cleanup (de-bloating)

This template ships with components that many projects never use. Keep the project
as small as its use case: from the answers above, recommend removing every
component the project will not use, and list each one in `prune` in the
configuration. Explain what each removal gives up, ask only about the uncertain
ones, and include the final list in the plan presented for approval. Deletion
happens only on `configure --approved` (or an approved `update-config`, whose
preview lists every path) and cannot be undone except through Git.

| `prune` entry | Removes | Recommend removing when |
|---|---|---|
| `data_analysis` | `addons/data_analysis/`, the analyzer and fetcher roles, their tests | Tasks never fetch, inspect, or analyze datasets (`addons.data_analysis` must be false) |
| `template_history` | `docs/template-history/` | Almost always; it records how the template was built |
| `initialization_primer` | `prompts/template_prompt_initialization.md` | Almost always once configured |
| `configuration_interview` | `CONFIGURE.md` | Configuration is unlikely to be revisited, or the template copy suffices |
| `claude_integration` | `CLAUDE.md`, `.claude/settings.json` (hooks), `.claude/agents/` | Claude Code will not be used; this also removes the Claude hooks |

Never prune what the project still uses. The core (`scripts/`, `agents/` core
roles, `templates/`, `githooks/`, `logging/`) is not prunable. Add custom roles or
extra cleanup only through reviewed code changes.

## What Python enforces and what stays policy

Python and hooks enforce: config types, role definition files, claims and path
scopes, the state lock, workflow-owned files, read-only raw data, artifact hashes,
evidence freshness, the review_ref binding, a reviewer named apart from the
builder, the repair limit, validation timeouts, commit checks, and (with
the add-on) data inspection gates. Assistant policy covers: approval itself, actual
delegation and independence, compaction, sensitivity judgments, semantic criteria,
and keeping application work in Python. Python cannot authenticate approval, launch
agents, or compact context. Paths, states, and handoff fields are fixed in code.

Source fingerprints skip data, workflow artifacts, environments, caches, and build
outputs. Choose source roots that cover everything whose change should invalidate
a review. Files outside the selection are neither fingerprinted nor claim-checked.
Running a builder's processor writes to `data/processed/` or `data/generated/`
outside the task's paths; that is expected (Git ignores data) and is not a scope
violation. Declare outputs worth inspecting as `post` datasets (add-on).

Project validation commands cover project tests only; the harness's own tests run
with `workflow.py self-test`.

## Agents and roles

Ask which assistants will run and how they should split work. Each agent needs a
`WORKFLOW_AGENT` identity (Claude gets it from `.claude/settings.json`). Tasks must
declare `--path` scopes narrow enough that parallel agents do not overlap. Confirm
each role's `writes`, `tools`, `runtime`, `model_hint`, and `independent_of`, and
offer custom roles (for example a documenter) as extra `agents/<role>.md` files.

If independent agents are unavailable, ask for an independent review session or
explicit approval of a same-agent fallback (`approved-same-agent`). Builder, QC, and
testing remain even for small tasks.

## Data-analysis add-on

Enable it only when tasks depend on datasets. Copy `addons/data_analysis/config.json`
into the config as `analysis` and set `addons.data_analysis: true`. The add-on brings
the analyzer and fetcher roles (`agents/analyzer.md`, `agents/fetcher.md`, marked
`requires: data_analysis`). Ask which raw data sources to register in
`analysis.sources` (templates/source.json): origin, locator, target folder, whether
a parser is needed, credentials (environment variables only), and timeouts.
Registering a source approves fetching from it. Then ask about expected datasets (addons/data_analysis/templates/dataset.json), phases (pre/post),
formats, required columns and column rules, minimum records, size and file limits,
analyzer access (`metadata-only` or `approved-summaries`), profiling (off by
default), and provenance entries (addons/data_analysis/templates/provenance.json, or
`[]`). See addons/data_analysis/README.md for the semantics.

Ask which processed or generated outputs should be declared as `post` datasets so
the analyzer inspects them. A task that creates its own inputs cannot pass a `pre`
check before they exist: stage the inputs in an approved setup step first, or
declare them `phase: post`.

## Updates

Configuration changes after setup use `update-config`: preview the changes and
affected tasks, get approval, then apply with the matching `--preview-hash`.
Changing dataset scope resets active analysis scope; unrelated edits keep it.

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
