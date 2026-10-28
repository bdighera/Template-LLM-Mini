# Dependency reuse and installation policy

User requested the proposed policy update: inspect existing dependencies and suitable
maintained libraries before custom equivalents; propose new packages with purpose and
tradeoffs; install approved packages in project virtual environments and record them
in dependency manifests; keep the core workflow harness dependency-free.

Updated shared rules, README, configuration interview/default execution boundary,
initialization primer, workflow reference, and builder/reviewer/tester instructions.
Scoped configuration or task-plan approval can authorize specific installations.
Manifests/lockfiles belong in task scope and validation.source_paths; test with the
project environment interpreter. Package installation is setup; external evidence
fetches retain their separate data policy. This is agent policy, not a new installer
or mechanical package-approval mechanism.

Independent dependency_policy_qc review passed across all nine edited files, with
configuration JSON parsing and diff whitespace validation. All 136 existing harness tests passed (96 core and 40 add-on); executed regression
results are archived alongside this record. No new tests mirror the prose-only change.
Template maintenance was registered and claimed as routine T003 in an isolated
configured copy; the reusable checkout remains unconfigured.
