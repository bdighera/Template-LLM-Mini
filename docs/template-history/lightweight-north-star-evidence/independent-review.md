review_ref: 22e298dff283

Independent review: PASS. No blocking findings.

Reviewed the current diff for AGENTS.md, NORTH_STAR.md, templates/agent_rules.md,
all three core role definitions, CONFIGURE.md, README.md, docs/workflow.md, and
prompts/template_prompt_initialization.md. Checked scripts/workflow.py guidance(),
configure/update-config, context(), source_roots() and fingerprint() against the
new claims. The template source remains unconfigured (.workflow has only lock).

The concrete template vision matches the approved lightweight purpose. Feature
plans must cite a principle and explain alignment; reviewers compare that claim
with the diff and unresolved conflicts need an explicit user decision. Setup
proposes approved project-specific direction rather than silently inheriting the
template vision. Configuration and context limitations are accurately disclosed:
commands do not author the vision, role instructions point workers to the file,
and assistants include it in validation source paths for stale-evidence detection.
The maintenance handoff also includes NORTH_STAR.md in fingerprinted source_files.

No new checkpoint gates, scheduling, configuration fields, runtime behavior or
dependencies were introduced. No application-specific features were added.
README paragraph placement feedback was resolved. git diff --check passed.
No tests duplicating the policy prose are warranted; harness regression results
must be recorded by the coordinator and are not inferred by this review.

Limit: This establishes instruction consistency and implementation accuracy, not
proof that future assistants will follow policy or judge alignment correctly.
