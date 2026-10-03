# BE12: durable local schedules, monitors and commitments

## Shipped boundary

The existing Hermes cron ticker is the sole timing/execution authority. Its existing
per-profile tick lock now also scans canonical `state.db` schedule records. There is
no second timer, daemon, JSON schedule mirror, or Dots execution loop. The owned
JSON-RPC consumer creates paused records and explicitly activates them.

This slice certifies finite, local retained-artifact adapters. It does **not** certify
live inbox access, HTTP monitoring, arbitrary scripts, agent/provider loops, external
notification delivery, calendar writes, semantic extraction, or autonomous memory or
skill promotion. Unsupported kinds fail closed. A local imported source is labelled
`live_connection_verified: false`; supplied calendar availability remains a preview.

Legacy unbound cron preserves its existing behavior, occurrence/execution ledgers,
process-fenced workers, pending-slot recovery, and delivery safeguards. Strict legacy
agent/script/URL jobs fail before `_prepare_job_prompt`: that function can run scripts
and fetch a monitor URL before constructing an agent. A valid-looking owner cannot
bypass this gate. Legacy agents pass `skip_memory=False`; the former AGENTS claim of
`True` was incorrect.

## Authority, admission and recovery

`ScheduleRegistry` writes through SessionDB's existing transaction owner. Schema40 adds
schedule heads, immutable versions, occurrences, monitor observations/intents, bounded
condition grants, import declarations and the commitment tables. Existing sessions and
cron JSON stores are not rewritten or auto-imported.

- A schedule key includes principal, profile, owning agent and schedule ID
- A version pins canonical finite JSON and SHA-256; changing a version requires a
  paused exact revision and no accepted/claimed/unknown occurrence
- The persisted owner binding selects the stable agent even when `active_agent_id`
  changes. Principal, profile-home or owner-policy changes fail closed. The trusted
  resolver binds home, secrets, terminal scope and identity before any source read
- New controls are available only to the owning live human transport. Models cannot
  accept commitments, activate schedules or grant their own conditional actions
- Occurrence identity is a deterministic digest of schedule key, version and UTC due
  instant. Runtime command acceptance, occurrence/run association, next-due advance
  and lifetime check-budget debit share one writer transaction
- Each occurrence has its own existing runtime session/lease, claim generation and
  original deadline. An accepted unclaimed command may start after restart; a claimed
  command never automatically reruns. Death even between runtime claim and occurrence
  stamp becomes `outcome_unknown` and blocks overlap
- Pausing/revoking atomically cancels still-unclaimed admissions. A claim that won the
  race keeps its receipt and rechecks stop/policy/grant/deadline before reads/actions
- Unknown occurrences require pause and explicit sourced reconciliation. Reconciliation
  preserves the command history and tombstone, never replays or claims effects undone
- Expiry and depleted lifetime check budgets prevent admission. Errors consume their
  admitted check. No automatic retries occur inside an occurrence

Timezones are explicit IANA names. Supported triggers are UTC one-shot, anchored
interval (at least 60 seconds), and weekday/wall-clock time. Calendar triggers declare
first/second fall-back fold and skip nonexistent spring-forward times. Each selected
wall time yields one UTC occurrence. Backward clock motion cannot repeat tombstones;
forward jumps use an explicit grace window and `skip` or `latest` policy. Skipped ranges
are recorded; catch-up does not create an unbounded backlog. Only one outstanding
occurrence per schedule can be admitted. The scan is capped at 100 active schedules.

Each definition declares finite `max_checks` (lifetime), `max_bytes` (per occurrence),
and `deadline_seconds` (at most 60). These deterministic local adapters cannot launch
chargeable provider calls. Finite functions check the original deadline at source
boundaries; there is no false claim that Python parsing or an OS read can be forcibly
cancelled in the middle. A version cannot reset consumed lifetime check budget.

## Monitors and conditional work

A monitor names its question, exact local artifact IDs, predicate version, cadence,
expiry, budget and `record_only` notification policy. Each check resolves and verifies
actual immutable current-head bytes under the owner's live project grant. Observed
source versions/digests are retained.

Installed predicates:

1. `normalized_text`: Unicode NFKC and whitespace normalization, compared by digest
2. `json_fields`: exact selected top-level fields, compared as finite canonical JSON
3. `threshold`: a finite numeric field with `gt/gte/lt/lte/eq`; alert on false-to-true

The first successful observation establishes a silent baseline. Cosmetic changes do
not alert for the selected normalization. Missing bytes, missing fields, wrong MIME,
malformed JSON, budget exhaustion and revoked sources are unhealthy, never unchanged.
The previous healthy baseline is retained on errors. Predicate selection is explicit:
whitespace-sensitive code should not use a whitespace-insensitive predicate.

A successful observation, baseline replacement and meaningful-change notification/
action intent commit atomically. Intents are durable and visible through schedule
status; `recorded` is not `sent`. No notification authority is inferred from a question.

A condition may declare only an immutable local review action. Observation alone
produces `awaiting_authorization`. An explicit human grant pins the schedule digest,
project and exact action/input digest, expiry, freshness bound and maximum fires.
It is revalidated and consumed atomically at the matching observation; schedule stop,
revocation, expiry or changed target blocks it. No standing external communication or
remote mutation permission is inferred. There is no installed live event/webhook
adapter; repeated local tick delivery is deduplicated by canonical occurrence.

## Bounded background reviews

Review jobs pin one to sixteen immutable source versions and an owner. Skill reviews
also pin a BE11 workflow version/digest. The shared
`hermes_cli.workflows.resolve_executable` checks the exact approved/nonrevoked version,
current grants and template pin; no active pointer or newer artifact version is loaded
on recovery. Reviews verify bytes/schema and produce an honest completion receipt.
Semantic review remains `human_review_required`; `memory_ingested` and `skill_promoted`
are false. No primary memory is inherited and no skill is self-approved.

The `weekly_review` adapter reads the authoritative accepted commitment registry. It
cannot invent obligations or reopen terminal/superseded work. Its receipt retains ready
and waiting records with the original schedule's bound deadline.

## Imported inbox, commitments and correspondence

`runtime.inbox.prepare` consumes a pinned JSON artifact from one selected source. The
snapshot contains schema version, source ID, capture time and messages with IDs,
thread IDs, offset timestamps, participants, attachment references and body. Selection
is exact thread IDs or a bounded time range of at most 31 days, up to 500 messages.
Related selected messages are grouped; current snapshot participants, attachments,
source spans and unresolved date wording remain attached.

English lexical classifications (`request`, `info`, `decision`, `waiting`) are proposals,
not semantic certification. Urgency is never inferred. Candidate identity deduplicates
overlapping imports of the same source/message content. Only explicit owned
`runtime.commitment.accept` turns a candidate into an obligation, retaining operator
owner/outcome and an optional offset+IANA-zone due/check time. Updates require exact
revision and immutable evidence. `done`, `cancelled` and `superseded` cannot reopen,
including after reimport or weekly review. Supersession names a distinct active accepted
successor. History remains append-only.

Correspondence drafts preserve exact recipients/content/source references, flag possible
new promises and create no commitment. `runtime.correspondence.receipt` can link only an
existing confirmed BE06 `correspondence_send` effect with exact target and input digest.
It never sends. This slice installs no live correspondence sender; its ledger test uses
a declared synthetic local effect adapter.

Calendar previews validate supplied participants, IANA timezone, offsets, covered window
and busy ranges. Capture age/staleness and supplied/unverified identities are explicit.
They produce up to ten candidate slots; they do not claim current live availability,
recipient verification, invitations, reservations or a calendar write.

Public imported-source results have a 120KB pre-write bound. Oversized previews reject
before candidate writes; oversized reads return an explicit error instead of silently
losing evidence. Schedule status exposes bounded occurrence/intent windows.

## Delivery capability matrix

| Path | Execution receipt | Delivery capability / ambiguity |
| --- | --- | --- |
| BE12 finite local monitor/review | Canonical command + occurrence; claimed work never replayed | `record_only`, durable intent, `not_requested` delivery |
| Existing cron agent/script | Existing process-fenced execution and scheduled-instant ledger | Existing delivery queue and dead claimed-send policy retained; unknown sends are not blindly retried |
| BE06 gateway outbox | Separate effect/execution records | Adapter acknowledgment can confirm; uncertain acceptance remains `outcome_unknown` and requires reconciliation |
| Confirmed retry-safe adapter | Only the adapter's declared dedup/query guarantees apply | No global exactly-once label; delivery failure never turns completed execution into an unexecuted job |

The inherited gateway's known-rejection retry semantics must not be confused with
unknown-send recovery. Nothing here widens either rule or retries an accepted effect.

## Migration and rollback

`runtime.schedule.import` accepts a human declaration naming the source authority,
source ID, paused/retired state and an empty unresolved-occurrence list. The ID is
`import_` plus a deterministic source digest. Active or unknown source occurrences are
rejected, and imported jobs remain paused. The declaration/command/run is retained;
`foreign_cutover_verified: false` makes clear this local import has not contacted or
stopped Dots. An operator must verify the old executor is retired before activation.

Do not activate both Hermes and Dots Runner. Dots remains a later transport/projection
consumer. Pause, drain/reconcile all old claims, retain tombstones and snapshot before
ownership/schema rollback. Never restore an older scheduler against unresolved work
or silently reinterpret unsupported strict jobs as legacy permission.

## Validation scope

Focused tests exercise real owned RPC dispatch, project ACLs, artifact publication and
bytes, actual SessionDB reopen, the existing cron `tick`, runtime commands/leases and
source parsing. They cover baseline/noise/change/outage, finite condition grants,
atomic admission rollback, claim loss, pause/version/import, DST/clock policy, immutable
BE11 review/revocation, false commitment, selected-inbox classifications, terminal-safe
weekly review, draft-versus-proof, availability-only preview and A→B→A identity scope.
Inherited scheduler/occurrence/delivery suites are regression gates. These local tests
are not live provider, mailbox, calendar, hostile-tenant or distributed delivery proofs.
