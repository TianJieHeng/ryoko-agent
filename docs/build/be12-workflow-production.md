# BE11/BE12: grant-fenced scheduled draft production

## Implemented local slice

The existing Hermes cron ticker can execute a pinned, human-approved workflow as
`kind: "workflow_draft"`. This is actual bounded Markdown production with retained
output bytes, not an immutable-source inspection receipt. The original `review`
adapter remains inspection-only and never claims to have produced a brief.

Creation is paused. Human `runtime.schedule.grant` authorizes the exact schedule
version/digest, project, workflow/version/digest, fixed parameters, local source
binding policy and destination, with an expiry, freshness ceiling and maximum
number of fires. Activation needs that grant. The grant debit, occurrence,
command acceptance, next due instant and schedule-budget debit share one existing
SessionDB transaction. Restart of accepted work neither renews its deadline nor
debits a second grant. Failed attempts do not refund a fire. Exhausted grants stop
new admissions and report `schedule_grant_required`; they do not authorize a
fallback adapter or automatically create a replacement grant.

The first producer supports workflows whose steps are all `render_markdown`.
It uses `agent.workflow_runtime.execute_workflow`, including canonical approved
example templates pinned to `artifact_templates`. Other workflow/domain adapters,
agent loops, scripts, model calls, remote connectors and external delivery are
explicitly unsupported in this scheduled slice.

## Schedule specification

The existing schedule envelope (timezone, trigger, missed-run/overlap policy,
budget and expiry) is unchanged. Its new specification has exactly these fields:

```json
{
  "workflow_ref": {"workflow_id": "approved-brief", "version": 1, "sha256": "<exact SHA-256>"},
  "parameters": {"topic": "Project status"},
  "source_bindings": [{"parameter": "notes", "artifact_id": "<owned local artifact>"}],
  "destination": {"kind": "project_artifact_drafts", "project_id": "<same project>"}
}
```

`source_bindings` is optional in effect (an empty list), not in shape. It accepts
at most eight explicit retained-local text/plain or text/markdown artifact IDs.
At each fire, current heads are resolved to exact immutable version/digest pins,
then inserted into declared string parameters. Bound parameters cannot also be
fixed parameters. This permits a real refresh after an authorized local source
update without granting a URL fetch or live-service connection. Fixed parameters
and resolved values must pass the existing workflow input schema. All source
bytes, including pinned template baseline/assets, plus produced bytes must fit
the per-occurrence schedule byte budget. The renderer also bounds output during
interpolation; the original absolute deadline is checked between bounded steps.

## Storage and authority

A dedicated `ScheduledWorkflowRun` is bound to the live canonical occurrence,
command, owner/policy, lease generation, grant and original deadline. The
interpreter rechecks its exact workflow and parameters. The result-storage edge
accepts only descriptors from the canonical produced manifest. This authority
cannot enter artifact-control RPCs, mint human approvals, publish project heads,
run model tools or promote a workflow.

Each production has a canonical Mission, a `workflow_runs` pin, the existing
schedule occurrence/command, and the existing effect journal plus actor-private
immutable result blobs. Mission state is waiting for human review; no completed
mission, verified semantic review or accepted output is fabricated. No new
schema/table, queue, ticker or runtime configuration is introduced (schema 44 is
unchanged by this slice).

Successful occurrence results expose `draft_only: true`,
`publication_state: "human_review_required"`, workflow/mission IDs, input digest,
exact sources, byte totals and per-output descriptors. Schedule snapshots expose
the latest five production occurrences and an honest `history_truncated` flag;
known older occurrence/output IDs remain addressable. Successful output storage
is not a project artifact publication or an external delivery receipt.

Lost claims and ambiguous storage acknowledgments become `outcome_unknown` via
the existing recovery path. They block replay and further admissions until manual
reconciliation. Confirmed retained bytes may be inspected even after workflow
revocation, but uncertain or failed production cannot be published through the
review bridge. Missing, corrupted or unconfirmed bytes are not served or rerun.

## Exact review path

1. `runtime.schedule.get` / `.list`: inspect occurrence result and output index
2. `runtime.schedule.output.get`: request `project_id`, `schedule_id`,
   `occurrence_id`, `output_index` and optional bounded `offset`/`limit`; receive
   complete digest-checked chunks with private-draft status
3. `runtime.schedule.output.prepare`: use the same identity, a new human
   `command_id`, and the inspected `expected_sha256`; receive the ordinary
   `ArtifactProposalResult`
4. `runtime.schedule.output.publish`: reuse the same review command/inputs and
   its exact `approval_id`/`approval_digest`; consume a fresh one-use approval and
   publish a new project artifact through the normal effect/catalog path

Review does not rerender, adopt a scheduled generation, reuse its production
grant as publication permission, or replace an existing canonical brief head.
It creates a new artifact. Old source/template pins and older drafts are retained.
The scheduled workflow must still be approved for preparation/publication.
Cancelled/expired review claims do not gain a new generation or replay effects.

## Validation and remaining qualification

See `be12-workflow-production-validation.json` for the exact 16-file canonical
runner lane: **176 passed, 0 failed**, including 17 new end-to-end production
cases and seven pure contract cases. The lane covers real RPC ownership, two
isolated profiles and wrong transport, the real cron tick, private blob storage,
canonical template reuse, fresh approval consumption, source-head refresh,
restart/deadline/grant preservation, policy/revocation stops, byte budgets,
corruption, fsync ambiguity and no replay. Existing schedules, workflow,
artifact/effect/approval, notification and migration suites remain green.

This receipt qualifies local synthetic backend behavior only. Actual deployment,
user schedule activation, connected research/inbox/calendar sources, external
notifications/publication, arbitrary domain producers, model or agent workflows,
Windows/macOS immutable-storage support, visual/client acceptance and empirical
benefit remain outside this slice. No live schedule, service, profile, secret,
connector, deployment or Git state was changed by the implementation worker.
