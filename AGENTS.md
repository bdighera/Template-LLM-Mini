# Configure this template first

This file is replaced by project-specific instructions when the project is
configured. Until then:

- Shared rules: templates/agent_rules.md. They apply before and after
  configuration; hooks enforce the mechanical ones.
- Initialization primer request: show prompts/template_prompt_initialization.md in
  full, keeping its <placeholders>; showing it does not execute it.
- Projects start from a copy: `workflow.py new DEST` clones this template with a
  fetch-only `template` remote. Configure the copy, not the template checkout.
- Feature planning and review: consult NORTH_STAR.md and follow the alignment
  policy in templates/agent_rules.md.
- To configure: read CONFIGURE.md and templates/config.json. Present every
  interview topic at once, with recommended defaults, and resolve missing or
  ambiguous answers. Then present the brief, project-specific North Star, configuration, proposed
  changes, and
  validation plan for explicit approval before `configure --approved`. Recommend
  pruning every template component the project will not use (CONFIGURE.md,
  "Template cleanup").
- Use `validate-config` for read-only preflight. Configured projects change their
  configuration only through the `update-config` preview/approval process.
- Command reference: docs/workflow.md (setup: README.md). Data analysis:
  addons/data_analysis/README.md (only when that add-on is relevant). Decisions:
  logging/README.md.
