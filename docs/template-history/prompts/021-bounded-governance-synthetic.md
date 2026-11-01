# Approved bounded governance and synthetic testing plan

Approval in the 2026-11-01 conversation: "Approved. Get started. All assumptions
are considered correct."

Approved work:

1. Record bounded task authorization separately from registration and claims.
2. Renew approval only for material changes to scope, permissions or direction.
3. Provide a proportionate maintenance path under an approved standing policy.
4. Compare actual changes and declared output evidence with the approved boundary.
5. Report actual tool enforcement coverage and its limits.
6. Add versioned reusable synthetic datasets and isolated synthetic runs under a
   configurable local data/synthetic root, with manifests, integrity checks,
   provenance, explicit inspection phases and governed cleanup.

Three increments: authorization/maintenance; scope review/enforcement reporting;
synthetic lifecycle/documentation/final validation. Align with North Star user
control, focused work, evidence before claims and small adaptable core.

Accepted assumptions: implementation follows this explicit approval; keep the core
standard-library Python; no new dependencies or external services; existing
configured projects retain legacy behavior until approved adoption; no automatic
data migration/deletion, remote-storage integration, publication or agent launching.

Template maintenance tracking remains isolated in a disposable configured copy at
/tmp/bounded-governance-maintenance. The reusable template stays unconfigured.
Read-only independent review is required by templates/agent_rules.md; no parallel
editing agents were used.
