# Workflow and reviewed follow-through controls

This bounded frontend slice uses the existing Hermes runtime authority. It adds no credentials, schedule engine, local persistence or outbound communication adapter.

## FE06: workflows and finite local schedules

- `WorkflowPanel` supplies project/workflow/version selection, workflow inspection, typed scalar input fields, exact mission/revision binding, prepare, separately confirmed output publication, retained command inspection/cancellation, and schedule health inspection with revision-bound pause/resume
- Editing prepared inputs disables publication but retains the original command. New preparation is blocked until an explicit `runtime.artifact.cancel` or status read acknowledges a terminal result. Unknown cancellation remains unresolved. Closing or changing connection does not claim cancellation
- Prepared runs show selected immutable workflow version, parameter digest and exact output proposals. Publication consumes ordered per-output approvals, does not assert atomicity, and does not assert mission completion or delivery
- Advanced workflow commands additionally expose bounded Markdown workflow authoring, source/accepted-mission provenance, evaluations with tuning and held-out cases, separate lifecycle decision prepare/commit, retained runs, and command status/cancel
- Accepted-mission creation resolves the owned durable session through `runtime.snapshot`; caller-supplied session/schema/identity fields are rejected
- Schedule command controls cover create (paused), list/get, exact-revision pause/resume/revoke, bounded local review grants, and source-backed reconciliation without replay
- Monitor UI separates source failures, unknown outcomes, initial baseline establishment, successful no-change checks and matched changes. It shows last successful check, next due, expiry, IANA timezone, explicit DST fold/gap, remaining budget and retained occurrence/intent states
- Backend notifications are record-only. Quiet hours, digest, snooze, durable dismissal and external notification/delivery retry are explicitly unsupported. Observation does not grant conditional-action authority

Commands: `/runtime workflow help` and `/runtime schedule help`. JSON is an advanced authoring interface; the desktop flows above use labeled fields. Definition JSON excludes runtime-owned schema fields. Monitor predicates omit `version`, which is injected as version 1. Domain-step authoring, templates, demonstration and export remain outside this selected frontend slice.

## FE08: reviewed commitments and correspondence

- `FollowthroughPanel` provides active review, candidate inspection, owner/outcome correction, optional explicit check time/timezone, confirmed acceptance, leaving a candidate unaccepted, exact sourced draft creation and canonical-ID inspection
- Active review is read-only and rejects terminal/unaccepted records in an active response. Candidate acceptance requires an explicit action with the reviewed revision. Source wording never becomes a deadline or owner automatically
- Advanced commands support bounded imported-inbox preparation, accepted obligation list/get/update with immutable evidence, waiting-only review, and availability-snapshot calendar preview
- Backend terminal obligations cannot reopen. Candidate decline is not durably stored; leaving a candidate unaccepted creates no work. No meeting speaker attribution, live inbox/calendar check, day/week capacity/travel planning, or calendar writes are claimed
- Draft content, recipients and source references are immutable under their draft identifier. Exact duplicate input is backend-deduplicated; edited content/recipients require a new draft identifier and review
- The backend returns a canonical correspondence ID different from the user-selected draft identifier. The panel retains the returned ID for inspection
- Correspondence receipt attachment associates existing confirmed exact send evidence; it never sends or certifies provider delivery. A send receipt is displayed as delivery unknown, including when arbitrary provider receipt fields claim more
- No send, queue, unattended reply, new commitment from draft text, or automatic delivery retry is offered. Possible promises remain review flags
- Explicit command inspection/cancellation is available for retained or uncertain mutations. A failed read never becomes an empty-inbox or no-open-work result

Commands: `/runtime commitment help` and `/runtime correspondence help`.

## Ownership and verification

Both panels capture the exact request transport, selected session and connection state. Context changes remount their ephemeral state; late reads and confirmations cannot cross scopes. Busy guards coalesce repeated clicks. Neither panel navigates nor moves focus when a result arrives. Chrome is provided for all nine current locales using feature-local maps and existing desktop primitives.

Separate integration whitelists:

FE06:
- `apps/shared/src/runtime-workflows.ts`
- `apps/shared/src/runtime-workflows.test.ts`
- `apps/desktop/src/app/runtime/workflow-panel.tsx`
- `apps/desktop/src/app/runtime/workflow-panel.test.tsx`

FE08:
- `apps/shared/src/runtime-followthrough.ts`
- `apps/shared/src/runtime-followthrough.test.ts`
- `apps/desktop/src/app/runtime/followthrough-panel.tsx`
- `apps/desktop/src/app/runtime/followthrough-panel.test.tsx`
- `docs/build/frontend-followthrough.md`

Package exports, TUI command wiring and panel mounting follow the phase integration boundaries. Focused validation exercises helper behavior and rendered interactions rather than source text. These isolated tests do not certify a live provider, native Electron packaging, visual screenshots or the whole P09/P10 release journey.
