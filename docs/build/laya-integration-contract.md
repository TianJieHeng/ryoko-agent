# LAYA systemone integration contract (ADR 013)

Recorded 2026-10-05 against `37d78be063d7876c92c54bc4a4b91ec8ace7fdd6`.
The [build plan](../../LAYA_Integration_BuildPlan.md) defines implementation;
[readiness](laya-integration-readiness.json) records evidence and open owners.

## Decision and compatibility

Keep the existing literal-private-IP, certificate-pinned mutual-TLS `LanTransport`
and typed `/v1/decide` protocol unchanged. Add an explicitly selected
`laya_systemone` adapter. DP16 v1 remains readable and executable; v2 has bounded
native independent stages (need/effort/family, inclusion, verification). No other
point acquires effects. A classifier never grants authority or changes budgets.

The operator supplied POST `https://laya.ryoko.okinawa/v1/systemone`, JSON
`state` plus a question-ID-keyed `questions` map, and a Bearer credential resolved
only as the profile-scoped `LAYA_API_KEY` reference. GET
`https://laya.ryoko.okinawa/health` is reported unauthenticated. Reported 200/401,
`i1` ONNX fp16, and inference/health-only route exposure are **operator reports**,
not qualification observations. The model starts at desktop login; the tunnel
starts at boot. Boot without login must be treated as unavailable until tested.

## Changed trust boundary

HTTPS verifies the exact hostname/certificate with system trust and never disables
verification, follows redirects, accepts per-request URLs or inherits ambient
proxies. The endpoint is a fixed allowlisted deployment destination. DNS routing
must reject non-public addresses before application bytes, including re-resolution
or redirect tricks. Destination grants are explicit, profile/identity scoped and
revocable; the transport must recheck them before each batch. One destination
slot survives caller deadlines until the actual in-flight operation exits.

Cloudflare terminates TLS and can access plaintext at its edge; Bearer headers
and packets also reach the origin. The tunnel is not end-to-end model attestation.
Operator evidence must cover account/access controls, origin authentication and
restriction, edge/origin/activity/log retention and deletion, traces/crashes, key
custody, and route isolation. No tunnel or server change is authorized here.

Private admission remains blocked. Authentication alone, shadow mode, redaction,
a config boolean, or an arbitrary object cannot authorize private transmission.
Only synthetic/public fixtures may reach an otherwise authorized test destination
until privacy qualification and exact data-category consent exist. No automatic
memory recall, attachment download, or credential discovery occurs in a turn.

## Frozen v2 protocol

- Canonical JSON state, renderer `dp16-systemone-v1`; data and descriptions are
  untrusted fields outside classifier instructions
- At most 12 KiB state, 32 KiB encoded request/response, 16 families, 32 candidate
  tools, 12 selected tools, 4 selected per family, 64 questions per batch, 3 batches
- Unique stage question IDs bound locally to complete typed requests; stage 2
  cannot start before stage 1 and stage 3 cannot start before stage 2
- Closed `choice` criteria only, explicit `unclear`; exact question/option sets,
  duplicate-key rejection, finite probabilities, sum tolerance 1e-6, winner and
  confidence consistency, ties abstaining, no inferred probability mass
- Up to 62 question receipts, one shared planner deadline/reservation/settlement,
  batch-level usage/circuit accounting, bounded receipt persistence
- All descriptors and permission/schema revisions bound in the catalog digest;
  one-to-one aliases never fuzzy-match a returned string to a capability
- Configured model/calibration/service hashes identify an expected release only.
  Measured loaded artifact hashes and provenance remain independent evidence.
  Tokenizer/export, contract, renderer/catalog and calibration changes invalidate
  release bindings. A vendor model alias is not a checkpoint digest.

The exact deployed Unicode flattening, duplicate-key handling, model alias,
confidence semantics, optional envelope fields, queue cancellation and latency are
still unmeasured. Strict local fixtures freeze a conservative supported subset;
changes require explicit protocol evidence rather than silent parser relaxation.

## Owner boundaries and rollback

The observer runs once per accepted front-door turn, never per provider retry or
tool round; shadow leaves provider bytes unchanged. Every result/receipt rechecks
run, generation and live identity. One planner budget covers all context, batches,
queue wait and receipts. A late result cannot install a bundle.

Reduction requires a qualified resolution and the actual schema owner before
cache decoration, at initial context or **successfully committed** compression.
A new task ID is not a cache boundary. Ordinary turns/resume preserve the exact
frozen prefix; search/describe/call recovers authorized omissions as tool data.
Revocation blocks dispatch even while a cached schema remains visible. Rollback
stops new decisions, fences late work, and restores incumbent schemas at the next
real boundary without rewriting a live prefix.

## L00 validation and resources

Baseline drift from the reviewed `a45b0d9` is documentation-only. No active GitHub
workflow exists; disabled workflows remain unchanged. Local resources: Linux
x86_64, 9 logical CPUs, approximately 9.7 GiB RAM and 27 GiB free workspace.
The existing supported Python 3.14.7 runtime was restored from its official pinned,
SHA-256-verified release, reusing the installed environment without dependency
mutation. Canonical tests use `scripts/run_tests.sh` with an explicit
`HERMES_PYTHON`; isolated homes and clean credential-free subprocess environments
are mandatory. No CI, paid compute or service call was used.

L00 checks validate JSON, local links, source fingerprints, disabled workflow
status, whitespace and absence of credential values. This phase freezes an
implementation contract, not deployment readiness.
