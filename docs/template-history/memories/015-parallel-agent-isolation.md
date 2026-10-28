# Parallel editing agent isolation

The user approved explicit language requiring each parallel agent editing repository
files to use its own Git worktree and dedicated branch. A branch alone does not
isolate a shared working directory. Never switch branches in another active agent's
working directory; read-only reviewers may share a checkout. The coordinator
integrates reviewed source changes, and task claims and path scopes still apply.

Updated shared rules, README, configuration interview, initialization primer,
workflow reference and builder/reviewer/tester instructions. The workflow reference
explains that .workflow copies, locks and claims are local to individual worktrees.
The coordinator maintains the authoritative board; workers must hold identity-matched
live local claims covering assigned paths and return reports/proposed handoffs.
Do not merge competing workflow-state copies. No global claim mechanism, automatic
worktree provisioning or runtime enforcement was added by this documentation update.

Independent parallel_policy_qc review passed after clarification of local claims,
with all eight changed guidance files examined and actual guard requirements checked.
All 136 existing harness tests passed (96 core and 40 add-on); executed regression
results are archived alongside this memory. No new
tests mirror the prose-only change. Routine template-maintenance task T004 was tracked
in an isolated configured copy; the reusable checkout remains unconfigured.
