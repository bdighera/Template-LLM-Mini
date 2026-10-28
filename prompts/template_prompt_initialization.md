Initialize customization of the harness at <path_to_template_llm_mini>.

My project:
- Name: <project_name>
- Purpose: <problem_to_solve>
- Intended users: <intended_users>
- Initial scope: <features_or_deliverables>
- Exclusions: <out_of_scope_items>
- Technology preferences: <languages_frameworks_and_environment_or_no_preference>
- Data: <known_inputs_generated_outputs_and_sensitivity_or_none>
- Agents: <assistants_that_will_work_on_this_e.g._claude_and_codex_or_one>
- Constraints: <technical_operational_budget_or_other_constraints_or_none>
- Success criteria: <observable_conditions_for_success>

Read AGENTS.md, templates/agent_rules.md, CONFIGURE.md, and templates/config.json
before proceeding; use README.md (setup) and docs/workflow.md for command details.

Run the complete configuration interview: present every topic at once, explain its
purpose and fixed constraints, prefill answers from the information above for my
confirmation, and recommend defaults. Do not silently accept defaults. Say which
settings Python and the hooks enforce and which remain assistant policy.

Cover in particular: whether the data-analysis add-on is needed; the roles and which
assistant plays each; how parallel agents split work (path scopes, lease length,
max_concurrency, review mode); validation source roots and commands; approval
boundaries; and which template components to prune so the project carries only
what its use case needs.

Agree a separate Git worktree and dedicated branch for each parallel agent that
will edit repository files. Never switch branches in another active agent's
working directory; read-only reviewers may share a checkout. Name the coordinator
and authoritative task board: worktree-local state, locks and claims are not
globally shared. The coordinator records authoritative workflow transitions and
integrates reviewed source changes; workers return reports and proposed handoffs.
Task claims and path scopes still apply.

For dependencies, inspect existing packages and consider suitable maintained
libraries before implementing equivalent functionality. Recommend new packages
with their purpose and tradeoffs, and agree the package manager, project virtual
environment, manifest/lockfile and installation scope. Configuration or task-plan
approval may authorize those specific installations; do not install before that
approval. Applications may use third-party Python libraries while the core harness
remains standard-library-only.

After the interview, present the completed project brief, configuration, proposed
changes, and validation plan for my approval. Do not configure the project,
generate project instructions, or start implementation until I explicitly approve.
The shared rules in templates/agent_rules.md then govern all later work.

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

If agent communication or orchestrator assignments are needed, discuss the optional
addons/agent_coordination component: JSON API first, MCP adapter, explicit identities
and task permissions, local approval, authoritative checkout, private retention and
separately approved dependencies. Default to not installed/not running; no automatic
model calls, external transmission or agent activation. Prune it when unused.

If coordination is retained, include its strict CI runner, dependency manifests and
schema freshness in the validation plan. If pruned, preserve the approved `prune`
record so CI can distinguish deliberate removal from an incomplete checkout.
