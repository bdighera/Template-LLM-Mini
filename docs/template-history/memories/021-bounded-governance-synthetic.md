# Bounded governance and synthetic testing data

User approved the final six-item plan and all assumptions on 2026-11-01.
Implemented bounded authorization, material escalation, compact maintenance,
actual-change evidence, enforcement coverage, and versioned synthetic datasets
with isolated runs. The core stays standard-library-only; new projects default to
bounded governance. Existing v2 configurations retain legacy behavior until an
approved update. Synthetic lifecycle settings are adopted explicitly; legacy
data discovery and fingerprints remain unchanged without adoption.

Read docs/governance.md and docs/synthetic-data.md for commands and limits.
No existing data was migrated or deleted, and no dependencies were installed.
No remote adapters, publication or model/agent launching were added.

Independent read-only governance_review passed all three increments at review_ref
4ccff1b18085. It independently executed 17 governance and 10 synthetic tests.
Each tracked task then executed the configured full self-test and strict
coordination check successfully against the reviewed sources. Final suite
sizes: 130 core, 44 data analysis and 15 coordination tests; strict coordination
ran 15 with zero skips and current schema. Local Markdown checks passed for
25 links/anchors. Local Linux validation required socket-enabled execution for
the existing localhost coordination tests; native Windows and hosted CI were
not executed in this session. Identities/approval flags remain assertions and
shell writes are not universally intercepted; these are local workflow controls.

Tracking is isolated at /tmp/bounded-governance-maintenance; the template remains
unconfigured. All three tasks are done and canonical evidence validation passed.
The bounded-governance-evidence folder preserves completed task state, exact
artifact bytes, decisions, approval/assignment logs, review, executed command
results, source fingerprints and final handoffs. Artifact refs are relative to
that evidence folder. Shared source changes were reviewed together; the three
increments intentionally bind to the same selected-source snapshot.
