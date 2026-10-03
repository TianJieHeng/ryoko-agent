# Durable conversation command schedules

A schedule with `kind: "command"` uses the existing schedule registry, profile cron
owner, `runtime_commands` journal, and `runtime_admission_queue`. The clock adapter
never constructs an agent, runs a script, or calls a provider. The ordinary gateway
prompt consumer executes the accepted command through `AIAgent.run_conversation`.

## Definition and controls

The existing `runtime.schedule.create/import/update/get/list` methods accept command
schedules. Every call requires the authenticated `session_id` and `schema_version: 1`.
Owner, profile, policy and project authority come from the producer session, never
from browser-supplied identity fields.

A definition has the existing exact fields:

- `schema_version: 1`, `schedule_id`, `version`, `project_id`, `timezone`
- `trigger`: `{kind: "at", at}`, `{kind: "interval", anchor, seconds}` or
  `{kind: "calendar", hour, minute, weekdays, fold, gap}`
- `policy`: `{missed_run: "skip" | "run_once", grace_seconds, overlap: "skip" | "queue"}`
- `budget`: `{max_checks, max_bytes, deadline_seconds}`
- `expires_at`, `kind: "command"`
- `specification`: `{prompt, session_id, authority_description}`

All timestamps are UTC Unix seconds. The target `specification.session_id` must be
the creating transport's canonical runtime conversation root; transcript compression
must not rewrite it. `prompt` is at most 16,000 characters, and its UTF-8 size must
fit `max_bytes`. This is a prompt-input byte limit, not an output-size promise.
`max_checks` bounds new admitted occurrences to 1–1,000; edits cannot replenish it.
`expires_at` is the standing admission authority expiry. `deadline_seconds` is
1–3,600, with the ordinary admission queue applying its own tighter launch TTL.
The accepted conversation budget policy snapshot remains authoritative for model
and tool work. The description is reviewable intent, not an executable policy or
permission to bypass approvals for external effects.

Creation is paused. Activation names an exact schedule revision. An edit is a new
immutable version, requires the previous version paused with no unresolved work,
and retains the lesser of the existing remaining fire count and new count.
Revocation is terminal. Pause/revoke stop *future admissions*: they do not cancel,
interrupt, or erase an already accepted/claimed mission. Use ordinary command or
mission cancellation separately.

`runtime.schedule.run_now` takes `project_id`, `schedule_id`, `command_id`,
`expected_revision`. The exact request key is idempotent across a lost response,
restart or later schedule revision. Reusing it for a different intent conflicts.
It returns `occurrence_id`, canonical `session_id`, `command_id`, `command_receipt`,
`state`, and `dispatch_performed: false`. A manual occurrence uses the caller's
command ID; a clock occurrence uses its deterministic occurrence ID. Always retain
both returned identifiers rather than assuming they are identical. Run-now neither
moves the regular recurrence nor bypasses pause, expiry, overlap or count bounds.

## Recovery and clock behavior

The existing per-profile cron ticker is the sole clock owner. An occurrence's queue
reference, command, count debit and recurrence advancement commit in one SQLite
writer transaction. Accepted but unclaimed launch reservations recover with their
original command ID. A lost claimed owner is `outcome_unknown`, pauses the schedule,
and is never implicitly replayed. Existing runtime recovery owns uncertain commands;
changing a schedule cannot declare their external outcomes successful.

Interval schedules are anchored UTC durations. Calendar schedules use their IANA
zone, skip nonexistent wall times, and fire only the explicitly selected first or
second ambiguous-time fold. `skip` advances past a missed grace window with a
retained skip receipt; `run_once` selects only the latest due occurrence. Neither
can manufacture a catch-up storm. `overlap: skip` records a skipped slot when this
schedule already has accepted/claimed work. `queue` retains separate commands, and
the ordinary per-conversation queue serializes their execution.

Global owner/profile pause is checked before clock accounting and inside the
admission writer. It does not debit counts or advance missed windows while paused.
The ordinary claim/dispatch gates enforce the same global pause after acceptance.

Off-session schedule resolution uses `recorded_owner_scope` and the source session's
immutable configuration enrollment in canonical `state.db`. Managed or copied
identities never resolve through the active-agent default or current configuration
head. Finite occurrence contexts retain the original configuration source for later
broker checks. Notification policy resolves its own recorded source session, which
can differ from the schedule creator's snapshot. Monotonic permission/archive
revocation is checked at resolution and again inside admission; a later regrant
does not revive an older schedule, notice or queued execution authority.

Execution requires a live authenticated owned producer session. The backend may
retain that connection independently of a browser tab. Detached clients do not
cause alternate execution. A command whose launch TTL expires becomes durably
blocked (`admission_state: expired`), never a new command on reconnection. The
current queue bounds launch TTL to the minimum of 300 seconds, the schedule's
`deadline_seconds`, and remaining authority lifetime.

Results, delivery IDs and delivery state come from the ordinary result/outbox
records. Notification failure or retry cannot rerun the scheduled command.
Schedule projections expose the most recent 50 occurrences plus total/truncation;
individual commands and immutable results remain readable through runtime APIs.

## Legacy cutover

`runtime.schedule.import` additionally takes `import_json` with exact fields
`authority: "dots_runner"`, `source_id`, `source_state: "paused" | "retired"`,
`unresolved_occurrences: []`, and `occurrences` (up to 100 records). Each history
record has `source_occurrence_id`, `due_at`, and terminal `state` (`completed`,
`failed`, `cancelled`, `skipped`). Inputs over that bound are rejected, not silently
truncated. Historical one-time schedules may be imported even after expiry but
cannot authorize new work. The adapter retains any larger legacy archive separately.

The schedule ID is `"import_" + digest(source_id)[:32]`, using the producer canonical
JSON SHA-256 function. Imported occurrence IDs derive from owner-bound schedule
identity and original source occurrence ID; retries preserve both IDs and receipts.
Importing historical occurrences performs no new work. The adapter supplies only
the remaining newly authorized fire count, not a reset of consumed legacy authority.

Every import remains paused until `runtime.schedule.cutover` receives `project_id`,
`schedule_id`, `command_id`, `expected_revision`, `source_id`, `retirement_receipt`,
and `unresolved_occurrences: []`. The adapter must first atomically freeze legacy
new admissions and reconcile active/unknown old claims, durably retaining this
retirement receipt before calling cutover. It must never restart the old runner on
an import retry or process restart.

The producer stores this trusted caller attestation and rejects unresolved IDs or
conflicting receipts. It cannot independently inspect a foreign runner:
`foreign_cutover_verified` stays false, and `cutover_attestation` exposes that
boundary. A paused foreign runner without a cutover attestation cannot activate.
