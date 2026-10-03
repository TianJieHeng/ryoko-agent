# Output context references and scoped controls

`runtime.memory.output.list/get` expose versioned memory references from a successful provider-call
input on an exact owned run. This is an input receipt, not a model's causal explanation. Coverage is
explicitly `fresh_memory_context_only`: inherited system/history context and arbitrary tool results
are not exhaustively enumerated. Exact sidecar JSON must occur in the supplied request before refs
are recorded; middleware removing it yields no false inclusion. Receipts are immutable per run and
include namespace, scope, fresh-packet hash and frozen-prefix hash. A zero-reference or unavailable
receipt is not evidence that the model used no other context.

`runtime.memory.output.control` requires the latest output's context hash, exact record/version and
namespace. The owned transport, live identity/policy, individual-store namespace and project grant
are rechecked. Foreign owners, stale output snapshots, record versions and project scopes fail closed.
`runtime.memory.output.control.get` returns the acknowledged status; it never equates queue acceptance
with model application.

- `ignore` suppresses the named version through a fresh contextual directive; it never deletes
  messages or memory history. A response override expires after the next supplied response context.
  Project/general ignores retain their explicit local applicability scope
- Response-only `correct` supplies bounded replacement text at a future turn boundary and does not
  create a durable preference. The following response gets an explicit expiration directive
- Project/general `correct` and `remove` must match the existing preference's durable scope and use
  the individual store's compare-and-swap writer. Creating a different-scoped preference is an
  explicit separate operation, not silent promotion of a one-off edit
- `remove` tombstones one exact record. The receipt says `tombstone_not_physical_erasure`; backups,
  previously supplied context and transcript history are not claimed erased

All controls leave the already-produced output unchanged. A request during an active turn says
`late_not_applied`; queued contextual changes wait for a valid future boundary. `memory_acknowledged`
records an actual store version, while `context_supplied` additionally identifies the later run to
which that update/directive was supplied. Failed/uncertain store writes are not replayed automatically.

Ryoko's external personal context-pack adapter currently has no verified record mutation contract.
No record IDs/versions are invented for opaque packs, no specialist receives the personal harness,
and no local hidden personal memory fallback is created. An unconfigured or unavailable harness
remains explicitly degraded/unsupported. Live harness mutation verification remains a setup gate.
