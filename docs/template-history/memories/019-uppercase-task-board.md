# Uppercase task board

User approved renaming the template's generated task board from `tasks.md` to
`TASKS.md`. Scope is this template; existing configured projects are not migrated.
The template remains unconfigured, with no project task state created for this work.

Renamed the tracked board without changing its content. Updated the generator,
generated README, active instructions, tests and CI hook smoke test. Historical
records keep their original spelling. Canonical state and its schema are unchanged.
Both filename spellings remain excluded from source fingerprints and protected
against direct edits. Shell protection now casefolds protected prefixes as well
as commands, preserving protection after the uppercase rename.

Independent read-only review passed after checking the shell comparison fix.
The added regression verifies uppercase directory entries, generated guidance,
and rejection of both board spellings as validation source paths. Core tests
passed (103), as did data-analysis tests (40); 15 coordination tests were skipped.
Optional coordination execution is unavailable in the current
interpreter: strict CI reports that `mcp` cannot be imported. No dependencies
were installed and no hosted CI run is claimed.
