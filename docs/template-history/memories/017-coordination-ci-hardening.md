# Strict optional CI and stacked PR validation

User approved hardening the existing coordination CI: automatic execution for stacked
PRs, no false-green optional suites, deliberate pruning, schema freshness and bounded
job duration. This updates PR #9 without creating another service pipeline.

Implemented standard-library scripts/ci.py with presence and strict-test commands.
Only an explicit supported workflow configuration prune entry permits an absent
coordination component. A retained component needs manifests and test files; the
strict runner requires importable dependencies, matching committed/generated operation
schemas and a nonempty successful suite with zero skips. GitHub outputs safely select
installation/test steps. All PR bases trigger automatically, main pushes/manual
runs remain enabled, and each matrix job has a 15-minute limit. Core jobs retain
normal dependency-free coverage; optional jobs run the strict component suite.

Eight new boundary regressions cover actual configuration pruning, incomplete
components, invalid prune metadata, missing dependencies, empty/skipped/failing
suites, GitHub output/error status, and stale/invalid schemas. Core discovery coverage
now includes the coordination test directory. No dependencies or service behavior changed.

Independent ci_hardening_qc passed after examining runner/workflow/tests/guidance,
executing eight boundary tests and checking the real schema and diff whitespace.
Full WSL regression passed (106 core +40 data +15 coordination =161 tests).
The strict runner also passed all 15 real HTTP/MCP tests with zero skips.

Automatic pull-request CI run 40675397479 passed all ten Linux/Windows jobs at
implementation commit a40f5bed0eba7523d72a3e9272b667ae58baec8b, proving the stacked
PR trigger. Core Python versions: 3.9/3.12/3.13. Strict coordination: 3.12/3.13.
Run: https://github.com/bdighera/Template-LLM-Mini/actions/runs/40675397479
The closing follow-up commit adds only archived request, review, decision and CI evidence.

The preceding service feature had left its maintenance-tracker North Star checkpoint
pending. Before starting this task, the user explicitly approved correcting that
tracker's synthetic brief to template maintenance and publishing its North Star in
a local-only commit. Existing task history was preserved; no vision file was added
to the reusable template or transmitted as part of this change. Routine task T006
was registered, claimed, reviewed and tested in that isolated configured tracker.
