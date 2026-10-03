# FE11 bounded on-demand opportunity review

This slice implements the selected-project opportunity candidate contract from FE11
sequence 5/7 using current canonical SessionDB evidence and live project grants.
It does not implement semantic cross-project discovery or background recommendations.

## Entry points and ownership

- `runtime.opportunity.discover`: an explicit owned UI request selecting 1–8 exact
  project IDs. Each source family reads at most 1–50 rows per project (default 20),
  with at most 1–50 returned candidates (default 20). The response reports source
  bounds, truncation, observation time and `complete: false`.
- `runtime.opportunity.list`: reads retained dispositions only and rechecks each
  candidate's current evidence. It does not discover or create candidates.
- `runtime.opportunity.disposition`: records `saved`, `dismissed` or `accepted`
  against an exact candidate revision, evidence digest and idempotency request ID.
- `runtime.opportunity.history`: bounded owner/project candidate and choice history.

The gateway resolves its existing live transport, session, agent identity and profile
before any operation. A finite host-owned control protects discovery/disposition;
model-run authority cannot mint it. Project grants are held while canonical evidence
and review metadata are read/written. No project catalog scan is used. Reading A
never enumerates excluded B, even if B is granted or referenced elsewhere.

Schema45 adds three review-metadata tables to the existing SessionDB writer:
`opportunity_candidates`, `opportunity_history`, and `opportunity_requests`. These
are not a second memory, workflow, commitment, mission or task authority. No new
runtime command, mission, accepted commitment, effect or approval is created by any
opportunity control. Candidate/history/request quotas fail closed instead of silently
throwing away suppression history. Schema44 migration is additive.

## Deliberately narrow rules

1. A committed current artifact head has an invalidated derivation: open the exact
   artifact for stale-source review. This rule does not read dependency content or
   promise that a refresh will make the output correct.
2. An explicitly accepted commitment remains `waiting` and its human-recorded
   due/check time has been reached: open its status review. No urgency, missed
   promise, live inbox state or permission to contact anyone is inferred.
3. The latest canonical workflow version is a draft without a successful evaluation
   reference: open that exact draft to choose evaluation cases. This neither
   predicts quality gains nor evaluates, approves, activates or runs it.

Every candidate has exact authorized evidence references, a concrete review action,
bounded benefit/effort language, and confidence labelled `deterministic_rule_match`.
There are no model calls, external source reads, network probes, personal-harness
writes, real profile configuration changes, teams or business-value predictions.
An empty scan means no match in these bounded source rows, not a completeness claim.

## Suppression, current evidence and acceptance

Stable candidate IDs bind owner, project, rule and source identity. Material evidence
hashes exclude irrelevant timestamps and command bookkeeping. Unchanged dismissed
and accepted candidates stay suppressed across restart, profile switches and retries
of old discovery requests. Saved candidates remain visible. Meaningful changed
source evidence can return as `proposed`, retaining its ID/history, increasing its
revision and explaining what changed. Harmless source revision bumps do not revive
an item; current views still cite the exact current source revision.

CAS and request idempotency run on the existing transactional SessionDB writer.
Concurrent distinct choices have one winner; duplicate requests return one retained
choice. Save and acceptance re-read the rule and material source digest in the same
transaction as the choice. A stale displayed candidate is rejected with
`opportunity_evidence_changed`; a changed candidate revision is rejected with
`opportunity_revision_conflict`. Grant revocation also blocks retained/replayed reads.

Acceptance records a review choice only. Its receipt explicitly has
`tasks_created: false`, `execution_authorized: false`, and
`next_step: open_existing_review_control`. The frontend may open the existing
artifact, commitment or workflow review UI. Creating/dispatching missions, approving
outputs and promoting workflows still require their existing separate controls.

## Recovery and verification

The existing supported single-actor owning-store bundle validates candidate,
history and request ownership, history continuity and referenced projects. A real
bundle/restore drill preserves dispositions and idempotency records; foreign owner
rows in any of the three tables are rejected. No live cutover or action replay is
performed by the drill.

Exact commands, test totals and source SHA256 values are recorded in
`docs/build/be11-opportunity-validation.json`. Python validation uses only
`scripts/run_tests.sh` with the isolated development interpreter/home/runtime and
`--scratch-parent /workspace/shared -j2`. No live services or credentials are needed.
