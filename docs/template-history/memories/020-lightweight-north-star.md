# Lightweight North Star planning and review

User approved the concrete plan and instructed implementation. The template vision
now states purpose, principles, worthwhile work and exclusions. Feature proposals
cite a section or principle and explain alignment; reviewers compare the assessment
with the diff. Conflicts and direction changes require explicit user decisions.
Narrow maintenance needs no separate assessment unless it changes direction.

Shared rules, all core roles, template entry instructions, README, configuration
interview, initialization primer and workflow reference carry the policy. During
setup the assistant proposes a project-specific vision for approval and writes the
approved text after approval. Configuration commands do not author it. Include
NORTH_STAR.md in validation source paths. Role context refers workers to the file;
it does not embed vision contents. Existing empty visions require approval first.

Independent review by north_star_review passed at review_ref 22e298dff283.
Self-test passed: 103 core and 40 data-analysis tests; 15 coordination tests skipped.
The configured maintenance validation reran the source self-test and passed.
A separate disposable setup check confirmed approved sample vision preservation,
generated alignment guidance and references in all three role context packets.
git diff --check passed. No new prose-mirroring tests, dependencies, fields,
scheduled reviews or checkpoint gates. Policy adherence and semantic judgment
remain responsibilities of agents and reviewers, not guarantees from Python.

Decision J000003 records the approved lightweight approach. Tracking is isolated
at /tmp/north-star-lightweight-maintenance; the reusable template stays unconfigured.
Existing configured projects are not migrated automatically.
