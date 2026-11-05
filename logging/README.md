# Decision journal

`journal.jsonl` is the canonical, append-only record; `journal.md` is a generated
view. Only `workflow.py` appends, under the workflow lock, and each record names the
`agent` that wrote it. The pre-commit hook rejects rewrites of earlier entries.

**What to record.** Significant decisions and feature milestones, with rationale,
outcome, and links to prompts, evidence, or memories rather than copied bodies.
Include alternatives only when they explain a tradeoff. Never record secrets, raw
data, tool chatter, or deliberation transcripts. Configuration, task creation and
completion, and applied config updates add milestones automatically; everything
else an agent must record with `decide --file decision.json` (templates/decision.json).
`status` (proposed/accepted/rejected) is not proof of user consent. Revise with
`decide --supersedes J000001`; the original stays intact.

**Reading it.** `journal` returns compact previews of the latest 20 entries within
6000 characters (`--max-chars` 1000..1000000). Narrow with `--task`, `--type
decision|milestone`, or `--id`; `--all` lifts the count limit, `--full` returns
complete records (still bounded; a record that does not fit is omitted whole and
counted). `journal --render` rebuilds journal.md without touching the JSONL. To
search by topic, use `workflow.py recall '<words>'`, which ranks decisions with
feature memories, excludes milestones, and flags superseded decisions.

**Citations.** `memory`, `handoff` and `checkpoint` reject journal IDs absent from
this journal. IDs follow line numbers in each copy, so one from another worktree may
name a different decision; the coordinator records decisions on the authoritative board.

**Failure handling.** If an append succeeds but rendering fails, the command still
succeeds, prints the saved ID, and warns: run `journal --render`, do not append
again. A corrupt canonical file must be repaired before further appends. If `check`
reports a done task with no completion milestone, run `reconcile TASK`.
Append-only is command policy, not tamper-proof storage; keep the journal in Git.
