# BE02 durable runtime contract

Schema: runtime envelopes v1; SessionDB schema 32. This extends the existing Hermes
runtime, SQLite writer and cross-process turn lease. It does not introduce a second
agent loop, scheduler, lease authority or transport. Existing token streaming/replay
continues separately from the restart-durable semantic journal.

## Ownership and persistence

The canonical compression-root session holds a monotonic owner-generation counter.
Its existing `session_turn_leases` row carries the current generation. Release, expiry
and lease-row cleanup cannot reset that counter. Runtime event/checkpoint/claim writes
check the live holder and generation in the same SQLite transaction. Actual model/tool
entry points check again before dispatch. Losing ownership cannot authorize a new call
or publish a stale result. Existing legacy holder-only operations remain compatible;
strict runtime paths use the generation fence.

The additive stores contain accepted commands, typed semantic events, one current
checkpoint and a consistent snapshot/cursor projection. Command acceptance and its event
are atomic. Command IDs and idempotency keys are scoped to the authenticated session
owner. An exact retry returns the original durable receipt. Reusing a key for different
content or an incorrect expected revision produces an explicit conflict.

A command is admitted for execution once. An accepted-but-unclaimed command can be
retried through normal live-session admission. A claimed command whose process died is
unresolved; recovery does not blindly replay model or tool calls. Completed duplicates
return recorded outcomes and the canonical transcript, not another generation attempt.
An oversized omitted outcome is explicitly unavailable on replay, never reconstructed
from a digest or silently rerun. Oversized inline requests are refused before dispatch;
artifact-backed large input belongs to the later artifact contract.

The common turn facade retains the existing conversation loop and lease lifecycle.
The TUI runtime submit path passes the durable receipt explicitly into its admitted
worker; it does not assume dispatcher ContextVars magically survive a raw thread.
Provider/tool started, completed and failed records capture bounded outcomes. Required
journal failure stops further dispatch and leaves uncertainty visible. Auxiliary provider
callback entry points retain the same generation check. Opaque external-agent/native
inner-loop transports remain unsupported in strict durable mode until their execution
ownership can satisfy the later provider contract.

## Control semantics

Submit, steer and cancel have versioned command envelopes. Steer completion means the
request was queued; cancel completion means a local cancellation request was made. Neither
claims that a remote provider stopped billing or an external effect was undone. Control
commands do not mark the active submit run completed. A second accepted submission does
not replace a currently claimed run's projection prematurely.

Approval commands are rejected until the exact-action capability/effect contracts in
BE05/BE06 exist. The new API's effect-protocol capability is disabled. Existing tool
confirmation rules remain in force; this phase does not certify exactly-once external
mutations, a privileged sandbox, or the later durable effect/delivery pipeline.

## RPC and generated clients

- `runtime.capabilities`: supported schema versions and executable operations for an
  owned live session; unsupported transports advertise their limitations
- `runtime.command`: closed v1 command envelope, exact operation-specific payload,
  expected revision, command ID and idempotency key
- `runtime.snapshot`: consistent committed state, references, compatibility status and
  durable cursor
- `runtime.events.since`: bounded semantic replay; invalid, expired, foreign-epoch or
  ahead-of-journal cursors return `snapshot_required` with a consistent snapshot/cursor

The existing session lookup is not transport authorization. Every new handler checks
actual transport ownership, active profile, immutable agent context and the full stored
identity binding. Actor IDs are synthesized by the server; client identity claims are
forbidden. Responses expose allowlisted state/correlation/reference fields, not raw
provider output, private protocol sidecars or configuration. Existing Python/Pydantic
contracts generate TypeScript/OpenRPC; no parallel hand-maintained wire format is added.

Journal retention advances an explicit cursor floor atomically with pruning. A gap or
unknown stored schema fails explicitly. Slow clients receive bounded pages rather than
holding a writer transaction or an unbounded stream buffer. Token-ring reconnect behavior
is not relabeled restart-durable.

## Compression, migration and rollback

Live compressed tips retain the original immutable logical identity. Reconstruction may
reuse it only when the existing canonical lease lineage proves that the physical tip is
in the same compression conversation. A copied record in an unrelated or explicitly
branched session does not suffice. Child fork markers are persisted before lease
admission; early identity claims no longer hide later constructor metadata.

Schema 32 is additive. Existing live leases upgrade their generation under their current
owner; competing owners cannot steal them. Runtime records carry their own version and
refuse unknown versions. Before downgrade, drain writers and retain a compatible backup,
configuration and enforcing runtime. Do not delete command history, reset generations,
or run older code over active identity-bound work. Backup/encryption/retention policy and
full deployment rollback certification remain the BE14/BE18 gates.

## Verification scope

See the BE02 journal entry and validation receipt for exact source and commands. Real
SQLite races/reopen/migration and real agent-loop fixtures use harmless deterministic
provider/tool doubles. These are not live provider billing/cancellation, personal-MCP,
OS isolation, hardware or production deployment measurements. The Dots repository remains
unchanged; these contracts are producer-side groundwork for its later adapter.
