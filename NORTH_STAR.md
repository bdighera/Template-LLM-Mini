# North Star

This vision guides maintenance of Template LLM Mini. In a new project copy,
replace it with the project-specific vision approved during configuration.

## Core vision

Help people and coding assistants deliver focused, reviewable work through a
lightweight, Python-first workflow with explicit user control and durable evidence.
The reusable core stays standard-library-only; optional capabilities stay optional.

## Guiding principles

- **User control:** obtain approval for concrete feature plans and changes in
  direction; agents never approve on the user's behalf.
- **Focused work:** use scoped tasks and claims, compact context, and separate
  worktrees for parallel editors.
- **Evidence before claims:** preserve provenance and uncertainty; require honest
  independent review and executed validation before completion.
- **Deterministic Python:** use Python for deterministic behavior and models for
  interpretation and judgment; reuse suitable approved dependencies in projects.
- **Small adaptable core:** configure project copies, prune unused components,
  and keep integrations explicit and optional.
- **Durable decisions:** retain significant decisions, feature memories, and
  handoffs so later work can recover the rationale.

## Worth building

- Clear configuration, feature planning, scoped execution, and review workflows.
- Reliable evidence tracking, validation, recovery, and concise agent context.
- Optional data and coordination capabilities with explicit boundaries and approval.
- Documentation and checks that describe and verify actual behavior.

## Exclusions

- Built-in provider infrastructure or automatic model and agent launching.
- Application-specific features in the reusable workflow core.
- Unapproved external data access, dependencies, publication, or changes in scope.
- Claims of production readiness based only on synthetic or limited local tests.

## Applying this vision

For each feature proposal, cite the relevant section or principle and briefly
explain how the change supports it. Flag conflicts for an explicit user decision
before implementation. Reviewers check the assessment against the actual diff.
Record approved direction changes and their rationale in the decision journal;
update this document with explicit approval. Narrow maintenance needs no separate
alignment report unless it changes direction.

These are agent and reviewer responsibilities. There is no scheduled review,
automatic alignment assessment, publication command, or North Star checkpoint gate.
