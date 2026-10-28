# Default subagent model

Behavior: `.claude/settings.json` sets `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`, so Claude
subagents with no model of their own (the built-in `general-purpose`, `Explore`, and
`Plan`, and those spawned by skills) run on Sonnet instead of inheriting the session
model. Roles keep the model their `model_hint` maps to through `sync-agents`.

Rationale: in a configured project, `/usage` showed built-in `general-purpose`
subagents at 16% of usage while inheriting Opus. Built-in agents have no file the
template can set a model in, and other people use this template, so the cheaper
default ships with it. Alternatives: a user-level setting only (does not reach other
users), or overriding `general-purpose` with a custom agent file (replaces its
system prompt).

Validation: `self-test` asserts the setting in `test_settings_wire_hooks_to_guard`.

Limitations: whether roles with `model_hint: inherit` (`builder`, `tester`) follow
this default or keep the session model is unverified. Projects can edit or remove the
setting in `.claude/settings.json`.
