# Current-feature README refresh

User requested that, after the CI work, README cover all current features, receive
accuracy QC, be submitted as a PR, and work stop for the day. Documentation-only
routine task T007 uses a dedicated branch based on the current PR9 implementation.

Reorganized README into a complete feature overview and practical sections for
requirements, approved project-copy configuration, task/agent workflow, evidence,
guards, journal, North Star reviews, both optional components, strict CI and the
repository map. It describes task dependencies/follow-ups/leases, handoffs and
fingerprints, roles and Claude generation, context checkpoint/resume, decision
history, data inspection/fetch/profiling/provenance, JSON/MCP assignments/recovery,
pruning and Windows/Linux validation. Volatile test totals stay out of README.

Accuracy boundaries are explicit: standard-library core versus optional packages,
policy versus mechanical checks, local claims versus global coordination, and
model findings versus approval/completion. No new runtime behavior is claimed or
implemented. The reusable template remains unconfigured and keeps its README marker.

Independent readme_accuracy_qc examined README against the workflow parser and
lifecycle, NorthStar, roles, guard/doctor/launcher, CI helper/YAML, Claude settings,
line-ending policy, both add-on guides and coordination source/manifests. It found
no material omissions or inaccuracies. Its optional pre-commit wording polish was
applied and re-reviewed with a final pass. Thirteen local links/anchors, nine
workflow command examples and the template README marker passed executable checks.
The existing full WSL regression suite passed: 106 core +40 data +15 coordination
=161 tests. No new tests mirror this prose-only change. Hosted checks are reported
on the documentation PR; this record captures local validation before PR creation.

Only README and historical request/review/decision/validation records change.
The documentation PR is stacked on PR9 while it remains unmerged; previous PRs
are not merged or otherwise modified by this task. Stop after opening and checking
the requested PR, as the user instructed.
