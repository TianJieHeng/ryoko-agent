# BE12 / FE06 bounded local monitor notification completion

This extends the existing finite retained-artifact monitor, canonical occurrence
ledger and BE06 `delivery_obligations` outbox. The existing per-profile cron tick
is the only time consumer. It groups notices and tries already-attached owned
JSON-RPC transports; it starts no service, provider, worker loop or external send.
No developer-home policy, live user schedule, credential or feature is activated.

## Explicit authorization and destination

A monitor definition can opt for `notify_policy: "local_runtime"` instead of
`record_only`. This declaration alone authorizes no delivery. A separate human
`runtime.monitor.policy.set` control admits a bounded notification policy through
the existing artifact-control command, lease and authenticated transport.

The destination is derived from that control's exact persistent conversation,
principal, profile and agent. Neither a client-supplied destination nor an
observation can change it. The immutable live policy identity is rechecked before
group admission and physical delivery, with current project read authority. A
foreign actor, conversation, transport, profile or policy cannot claim/retry it.
Changing the destination requires a separately authorized new schedule identity;
version edits cannot rebind old notification history.

Policy JSON fields:

- `kind: "local_runtime"`
- `timezone`: exact IANA zone
- `quiet_hours`: null, or `{start_minute,end_minute,fold:"both",gap:"next_valid"}`
- `digest_seconds`: zero for immediate, or 60 through 86400 elapsed UTC seconds
- `max_deliveries`: 1 through 1000 digest/single-notice admissions
- `expires_at`: exact UTC timestamp, no later than schedule expiry

Quiet hours are half-open local wall-clock intervals, including cross-midnight
intervals. Both instances of a folded minute are quiet. Nonexistent minutes have
no instants: delivery resumes at the first real local minute outside the interval.
Identical start/end is rejected rather than ambiguously meaning zero/all-day.
Digest windows are anchored to the oldest pending observation in UTC, not to local
clock repetition. Up to eight immutable changes are grouped oldest-first; further
items remain pending for later bounded ticks. No newest-value coalescing discards
intermediate meaningful changes.

## Controls and durable truth

All write controls require the exact schedule revision and an idempotent human
command identity. `runtime.monitor.snooze` takes explicit `until_at`, with null
clearing the snooze. It suppresses delivery, including retries, while checks,
finite check budget, baseline progression and health reporting continue.

`runtime.monitor.dismiss` persists the selected immutable notice dismissal. An
unchanged source produces no further notice. Deduplication binds schedule version,
predicate, immutable source versions/digests and semantic projection. A later
meaningful source version remains distinguishable from an older dismissed item.
If no physical attempt happened, dismissing one digest member retains the old
batch receipt and returns its undismissed neighbours to pending grouping. Already
accepted/unknown batches retain their immutable evidence and cannot be rewritten
or automatically replayed.

A policy edit does not grant old evidence new authority: old pending notices are
retained as `superseded_policy`; notices observed without policy remain
`awaiting_policy`. Both are inspectable pending work, with explicit hold reasons.
Pause, revoke, expiry, exhausted delivery budget and lost live grants prevent new
admission. Retained accepted/unknown receipts are not relabeled undone. Source
access failures remain unhealthy checks and never become no-change observations.

`runtime.monitor.notifications` returns policy, exact derived destination, expiry,
snooze, remaining admission budget, notice history, pending/total counts, holds,
and BE06 delivery receipts. History is explicitly bounded/truncated, with `next_cursor_json` fed back as
`cursor_json` on read to inspect older notices. Omitted older history is not a
claim of no pending items. Scope is retained local artifacts,
not proof of a live inbox or external-source connection.

## Same-client event and receipt protocol

`runtime.monitor.available` carries `delivery_id`, `attempt_token`, `sha256`, and
`notification_json`. SHA256 covers the exact UTF-8 JSON string. That immutable
JSON contains schema/schedule/policy versions, change/digest kind and every grouped
item's intent/occurrence ID, question, observed timestamp, predicate version,
previous/current immutable source references and honest retained-source scope.
The notification record is a canonical DB-backed immutable payload, not a
filesystem artifact or a runtime model result. The generic BE06 receipt's
`artifact_id` identifies that notification payload and its monitor MIME marks it.

Consumers deduplicate stable delivery IDs, validate bytes before display and read
persistent notice/dismissal truth on reconnect. `runtime.delivery.ack` uses the
existing exact attempt/digest-bound receipt. `artifact_received` means the full
immutable notification JSON bytes were received and validated; `text_received`
means its visible notification text was received/rendered. Neither means a human
read it. Partial receipts remain partial. `runtime.delivery.status` is read-only;
`runtime.delivery.retry` retries the same immutable payload under live policy,
quiet/snooze and BE06 three-attempt/deadline/backoff limits. It never runs a check,
reads a newer source, calls a model or dispatches a condition action.

Transport acceptance remains `awaiting_ack`, uncertain writes `outcome_unknown`.
Unknown writes are never automatically retried; a stale attempting write becomes
`outcome_unknown` after the existing local 60-second attempt window. Explicit
owned local repair is possible because stable local delivery IDs are deduplicable.
External notification adapters remain uncertified and unsupported.

## Qualification

See `be12-notification-validation.json` for exact command/log hashes and limits.
The fixture is synthetic, uses temporary homes, real RPC registration, actual cron
entry points and the real owned BE06 outbox/transport boundary. No live service,
macOS/Windows notification daemon, remote deployment, model quality, OS toast or
external sender is certified by these tests.
