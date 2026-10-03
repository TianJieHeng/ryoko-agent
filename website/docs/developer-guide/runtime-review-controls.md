---
title: Runtime review controls
---

# Runtime review controls

These JSON-RPC methods require an authenticated, live, owned session and its
profile-bound `AgentContext`. The client supplies `schema_version: 1` and the
live `session_id`. It cannot supply an actor, profile, lease holder or generation.
All records use the existing canonical SessionDB and writer transactions.

## Exact approval recovery

`runtime.approval.get` takes `approval_id` and returns:

- `approval`: the same bounded identity, digest, expiry and status projection as
  `runtime.approvals.list`
- `detail`: `reviewable`, `unavailable_reason`, `review_digest`, and the retained
  exact `review` when available
- `decision`: the recorded `once` / `deny` choice and resolution / consumption
  timestamps. An immutable decision record survives subsequent invalidation
- Exact input and artifact revisions only when the review is available
- `dispatch_performed: false`

Recovery is read-only, including after the original run ends. It never calls
resolve, consumes approval or retries an effect. A missing approval is an error;
missing review bytes are explicitly unavailable, never reconstructed from a hash.

The capability producer binds the review digest into the original
`approval_digest`. Generic action records retain the exact canonical JSON action,
including arguments, destination and operation class, in a separate bounded review
table. Ordinary list/get approval records, runtime events and snapshots continue
to exclude the private review payload. Small UTF-8 project-artifact bodies are
retained as exact base64 bytes with SHA-256 and MIME. Content larger than 64 KiB,
opaque binary content and missing historical reviews are unavailable on this
surface. Existing artifact-specific review workflows remain available.

Credential-shaped fields, registered vault values, known secret patterns and
credential-bearing URLs are checked using forced redaction. If a check would
change the material, the entire review is withheld; a redacted subset is never
represented as an exact review. This is not a general-purpose secret detector.
Producer adapters must continue to exclude credentials and unsupported sensitive
payloads from action inputs. Clients must not offer an exact approval from
`reviewable: false` or use these records to infer replay permission.

`runtime.approval.resolve` still requires the original exact live runtime owner,
policy, digest and expiry, and accepts only `once` or `deny`. Repeated decisions
are rejected; use `get` to recover an uncertain response.

## Owner/profile pause

- `runtime.control.get` takes an optional `operation_id`
- `runtime.control.pause` and `runtime.control.resume` require a stable
  `operation_id` and `expected_revision`

Mutation responses return both the immutable operation receipt and current
`control` state. Repeating identical request bytes recovers the original receipt;
reusing its operation ID for different bytes fails. A read of an unknown operation
returns `operation: null`. An old receipt can describe a pause while current state
already reflects a later resume; consumers must keep these facts separate.

Scope is the authenticated principal and profile, across that owner's agents.
The fence blocks new submit/artifact admission, queued reservations, command
claiming and subsequent checked model/tool/artifact/effect dispatch. Scheduled
producers use the same atomic writer fence; a pause must be checked before
schedule catch-up accounting as well as source reads and dispatch.

Typed schedule updates that only pause or revoke remain available while globally
paused; creating or reactivating work remains fenced.

The acknowledgment is explicitly `blocked_at_next_boundary`. Already dispatched
operations may finish and record receipts. Pause does not cancel provider work,
undo an external effect, abandon a claim, move accepted work to another agent,
refresh a budget or extend a deadline. Accepted and claimed counts are a current
snapshot, not proof that every external operation has stopped. Resume changes the
fence only; it does not replay a claimed or uncertain operation.

## Sequential missions

One canonical conversation retains one active execution slot and multiple
immutable historical mission identities. To create a subsequent mission, call
`runtime.mission.create` with a new `mission_id`, plus the exact
`previous_mission_id` and `previous_revision`. The prior mission must be completed,
cancelled or failed, and no accepted/claimed command may still own its work.
Archival and creation commit atomically. The old mission ID can never be reused.

`runtime.mission.get` with no mission ID reads the current slot. With a mission ID
it reads that exact active or archived mission. `runtime.mission.history` reads a
bounded current-plus-archived list for the owned canonical conversation.
`runtime.mission.receipts.list` accepts an optional exact `mission_id`.

Archived records are observations frozen at replacement, including retained
artifact, effect, delivery, approval-plan and budget references. The authoritative
effect/approval/budget/verification tables are not deleted or relabelled. A later
reconciled effect can have newer live state than the archived observation.
Original budgets and deadlines remain constraints on the next mission.

Every mission mutation from a history-aware client must include the exact active
`mission_id` and revision. Legacy controls omitting the ID work only before the
first archival in that conversation. After replacement, omission is rejected with
`mission_id_required`; an archived or otherwise non-active target is rejected with
`mission_identity_conflict`, even if its numeric revision matches the new mission.
This includes revise, verify, accept, pause, resume and cancel. Archived missions
cannot dispatch or be implicitly resumed.
