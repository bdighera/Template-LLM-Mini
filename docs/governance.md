# Bounded work and proportionate governance

New configurations default to `governance.mode: bounded`. Existing version-2
configurations without this section retain legacy behavior. Adopt it only through
`update-config` preview and approval. No task history is discarded. The optional
`synthetic` configuration follows the same adoption rule.

## Authorization, ownership and escalation

Registration records requested work. A live claim establishes temporary,
worktree-local ownership. Neither establishes approval. Before a standard task
starts, record its approved boundary using templates/authorization.json:

```sh
python3 scripts/workflow.py authorize T001 --file /tmp/task-authorization.json --approved
python3 scripts/workflow.py start T001
python3 scripts/workflow.py changes T001
```

The guard asks the user before an agent runs `authorize`, or `synthetic-clean`
with `--approved`; `--approved` alone is an assertion, not proof of approval.

Fill the objective, exact task criteria, canonical source path patterns, declared
output locations under data/ outside raw/, permitted data access, dependencies,
external activity, and a concrete approval reference. Empty permission lists mean
none. Keep transient input documents and review reports outside the source tree,
for example in a local temporary directory; the CLI retains its canonical records.
Do not put secrets or record-level data in approval records or manifests.

Approval authorizes ordinary implementation decisions and repairs within the
boundary. Renew approval for expanded objectives or criteria, paths beyond the
approved envelope, new sensitive-data access, external transfers, unapproved
packages, destruction, publication, or changed project direction. External actions
are not executed or sandboxed by this record. Agents must compare their intended
actions to the permissions and cannot approve for the user.

Amend using `authorize --file ... --approved`; an existing authorization may carry
approved replacement criteria. It invalidates affected review/test evidence. For an already-active legacy task
adopting bounded mode, its first authorization records the current Git HEAD as
the new baseline; changes already present are included in scope review.
`scope --path ...` may narrow or select paths inside the approved envelope without
another approval. Arbitrary glob containment is deliberately conservative: exact
patterns and descendants of an approved `directory/**` are supported. Widen the
authorization first when containment cannot be established.

A bounded task needs a committed Git HEAD. After configuration, review and commit
the configured project baseline before the first task; otherwise setup changes
will correctly appear in the task diff. Its start records that revision; the
change manifest compares current tracked and untracked source files with that
revision, including additions and deletions.

Several agents may run bounded tasks in one worktree. At start, pending changes
inside another non-cancelled task's paths become this task's baseline; any other
pending source change blocks start. Afterwards, a change outside this task's
paths is `attributed` to another task when it falls in the paths of a task that
is unfinished or finished after this one started (so a peer committing its work
mid-flight is fine). Attributed changes are excluded from this task's evidence,
so peers' edits never stale it. Changes no task accounts for block start,
reviewer handoff, and completion. A shell write into a peer's paths is credited
to that peer and stales its evidence, so the peer must re-verify before
completing; the write is detected, not prevented.
Ignored untracked files are not discovered by Git enumeration. Paths excluded as
data, workflow artifacts, environments and caches remain outside source evidence.

Source fingerprints include the authorization, actual change manifest and hashes
of declared output files. Declare exact output files or narrow folders, without
globs, to keep hashing bounded. Missing declared outputs are recorded explicitly;
they are not automatically required deliverables. Criteria and tests determine
which outputs must exist. Changes after review invalidate evidence. Hashes prove
byte agreement, not necessity, privacy or truth.

## Maintenance

Set `governance.maintenance_approval` to a real approval reference through the
configuration process to authorize a standing maintenance policy; `none` disables
it. Maintenance means small corrections and narrow fixes with no new capability,
new data permissions or changed governance boundaries. Uncertainty defaults to
standard. The classification is an agent/reviewer responsibility, not a semantic
judgment Python can prove.

```sh
python3 scripts/workflow.py add --title 'Correct typo' --criterion 'Correct wording' \
    --path README.md --prompt-text 'Correct the identified typo' --class maintenance
python3 scripts/workflow.py start T001
python3 scripts/workflow.py maintenance-finish T001 --file /tmp/maintenance.json
```

Use templates/maintenance.json for a compact change record, verification argument
arrays, verification rationale and final handoff. Commands execute without a shell
under the configured timeout. Empty command arrays permit appropriate manual
verification with an explicit explanation. This path requires a live claim by
policy, detects out-of-scope source changes, and retains one completion artifact
and a journal milestone. It does not require separate review and feature memory.

Maintenance cannot select data-analysis phases or mutate the synthetic lifecycle.
When it grows, run `classify T001 --class standard`, obtain explicit task approval,
and follow standard independent review, tests, memory and final handoff. The CLI
supports promotion only, so a standard task cannot be downgraded to evade review.

## Review and enforcement coverage

Reviewers compare actual changes with criteria, not merely paths. Explain why
supporting refactors are necessary; resolve unrelated changes before completion.
Review reports cite the current review_ref. Tool-supplied reviewer names and
approval flags are assertions; core agent identities are self-declared.

`doctor` and role context report coverage as automatically-enforced,
manually-invoked, policy-only or unknown. Hook configuration does not prove an
active editing integration. Guard edit must be invoked before writes when hooks
are unavailable. Shell source writes are not claim-intercepted. Completion checks
detect changes after editing; these controls are not an OS sandbox or an
authenticated access-control system. Claims coordinate agents within one
worktree; they do not coordinate across worktrees. Retention and external sharing need actual operating controls where
required by the project's data classification.
