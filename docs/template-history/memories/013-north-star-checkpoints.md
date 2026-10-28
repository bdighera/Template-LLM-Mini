# North Star checkpoints

Approved scope: periodic synthesis at ten completed tasks and every subsequent ten;
significant features require alignment before implementation and reassessment on
completion. Pending reviews block new starts/claims, while existing owners finish.

Implementation: Python detects thresholds and overdue adoption, creates an explicitly
unapproved draft, surfaces gates in workflow/session context, and binds publication
to the exact draft, task state, selected sources, repository source/docs and prior
vision. Publication requires explicit user consent and commits NORTH_STAR.md plus
its approval and recovery records, excluding unrelated staged work. Contributors
supply evidence-backed qualitative synthesis and flag conflicts with approved vision.

Validation: 136 tests passed (96 core, including 14 North Star cases; 40 data add-on).
Independent north_star_qc review passed after durable-clone metadata and claim-access
findings were fixed. Tests cover thresholds, real completions, significance, adoption,
blocking/renewal, stale approvals, commit failure/retry and clean-clone recognition.

Limitations: Python validates workflow structure and references, not semantic truth
or authentic user consent. External data remains subject to existing evidence rules.
The template checkout stays unconfigured; task T001 was tracked in an isolated copy.
