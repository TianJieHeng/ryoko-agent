# Bounded runtime budgets and admission

BE03 extends the existing SessionDB owner lease and AIAgent turn pipeline. It does
not introduce a second agent loop or certify vendor invoices. Schema 33 is an
additive migration; durable runtime envelopes remain version 1.

## Configuration and accounting

`runtime_budget` is opt-in and requires a configured BE01 identity. Its absent or
empty value preserves legacy execution for an unbound session. Once a session has
a budget root, removal or replacement of the policy cannot reset its ceiling.
See [the generated field reference](runtime-budget-reference.md). No real endpoint,
price, credential or identity is configured by this implementation.

Each admitted run has its own account beneath the original session root. Trusted
delegation binds children to that tree before dispatch. Subsequent turns and
restarts retain the first root until a later explicit mission boundary is
implemented. Reservations atomically check every ancestor for tokens, physical
attempts, wall allocation, provider slots, executor slots and optional cost in
integer micro-units. The existing iteration limit remains an independent local
limit. Child execution uses its own current owner fence; releasing a parent's
turn lease does not grant a child a new root or remove ancestor limits.

Reserve precedes dispatch. Settlements retain unknown usage conservatively;
measured overruns become visible debt and block further allocations. A timed-out
provider operation retains its concurrency slot because a local error does not
prove that upstream work stopped. Unacknowledged requests may therefore exhaust
capacity and require later reconciliation; they are never silently refunded.
Token mode explicitly reports cost as untracked. Cost mode uses operator-verified
worst-case route rates, not guessed pricing or a vendor invoice guarantee.

The initial supported provider contract is a single text-only OpenAI SDK chat
completion on an exact configured model and credential-free base URL. It forces
SDK retries off, bounds the full request envelope conservatively, sets the
verified output-limit parameter and applies per-request and root deadlines.
Every application retry and supported auxiliary call consumes another reservation.
Unsupported native/opaque loops, media, server-side tools, embeddings, MCP
sampling, memory service charges and other unbounded adapters are refused in
budget mode before dispatch. Only verified local todo handling and trusted child
orchestration currently have tool contracts. Later adapter phases must preserve
this refusal until they can enforce and report their own finite envelope.

## Admission and recovery

The durable runtime submit API uses bounded scheduling metadata referencing the
existing command journal; it does not store a second prompt. Acceptance and queue
admission are one transaction. Limits apply to each profile's SessionDB, not an
unproven global cross-profile quota. Active launches, queued count, per-principal
count, payload bytes, aggregate queued bytes, database/WAL size and expiry are
bounded. Principal fairness and a finite interactive priority boost prevent an
endless stream of new interactive work from starving older background work.

Acceptance pins the policy snapshot and absolute deadline. Waiting does not earn
a fresh budget. The existing TUI turn consumer owns execution. Maintenance
reconciles accepted work after restart without requiring another submission;
only an authenticated live session can launch it. Unattached work can expire
with a durable terminal result. Unclaimed interrupted launches may recover;
claimed operations remain uncertain and are never automatically replayed.
Disconnect does not cancel accepted work. Session close terminalizes pending work. Shutdown first closes this process’s
consumer, then terminalizes owned live-session references; unattached accepted
references remain recoverable or expire for a replacement consumer. A shared
profile-wide draining switch is not silently imposed on other live backends.
Already-dispatched uncertainty is preserved.
Legacy prompt paths are not advertised as globally queued by this new API.

## Cancellation

A task scope propagates cancellation to the existing agent interrupt, human wait,
child and owned-process mechanisms. Shared deadline scheduling avoids a permanent
watchdog thread per run. Process cleanup uses the existing registry's task owner
and a pre-run baseline, so older processes sharing a session task key are not
indiscriminately killed. This is lifecycle cleanup, not hostile-code isolation;
BE05 owns that boundary.

Receipts distinguish local state, upstream acknowledgment, pending handles,
uncertain effects and usable partial output. A finished local worker never implies
that a remote provider stopped billing or an external effect was reversed.
Unknown work and reservations remain visible across restart.

## Rollback and evidence

Do not downgrade to a binary that ignores established budget bindings or queue
records. Stop new admission, drain or terminalize accepted work, retain unknown
reservations and preserve the compatible database/configuration pair. Disabling a
feature must not erase spending, recreate a root or replay a claimed operation.
Focused tests use real SQLite writers, real AIAgent construction and OpenAI SDK
HTTP seams with deterministic transports. They do not constitute live billing,
provider cancellation, deployment or hardware certification. Exact check receipts
are recorded in the phase journal and validation manifest.
