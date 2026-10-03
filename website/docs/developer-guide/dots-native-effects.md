# Dots native page and computer effects

Dots retains its native page store and configured computer supervisor. Ryoko
owns command admission, exact approvals, policy, dispatch identity and uncertain
outcome recovery. These adapters do not provision a computer, token, credential,
remote endpoint or model-facing core tool.

## Trust and registration

`runtime.dots.register` is accepted only from the current session's attached
`StdioTransport`. It binds a configured `adapter_id`, kind, revision and bounded
grants to the producer's principal/profile/stable-agent identity. A display name,
Dot ID, caller-supplied URL, or `runtime.screen` service does not establish this
identity. Dots must resolve its local Dot/container mapping from the validated
producer identity and retain its own independent per-Dot isolation checks.

Page registrations intersect the immutable policy's project grants and live
project permissions with declared Space grants. Computer registrations intersect
`actions` with immutable `allowed_tools` entries `dots_computer_<action>`.
Registration CAS updates require `expected_revision` and a strictly newer
`revision`. Disable or narrow grants on revocation. Registrations are process
local; re-register the configured endpoint after a producer restart. This does
not authorize replay of any effect. A different live transport cannot replace a
registered peer. No credentials are stored in the producer registration.

## Proposal and dispatch

The Pydantic authority is `tui_gateway/contracts/dots_effects.py`; generate the
shared TypeScript/OpenRPC contracts with `scripts/gen_gateway_contracts.py`.

1. Call `runtime.dots.page.prepare` or `runtime.dots.computer.prepare`, with a
   stable `command_id` and the exact complete proposal. The command journal binds
   all proposal bytes; conflicting reuse is rejected. Page document fields are
   title, content, parent ID and archive state. Head version zero means creation.
2. Display the exact approval through the existing review-detail contract.
   `prepare` returns the approval ID/digest, action/input digests and expiry. It
   cannot mutate native state. The approval lives in the standard durable store.
3. After the owner's exact decision, call `runtime.dots.page.publish` or
   `runtime.dots.computer.execute`, repeating the unchanged command/proposal and
   approval ID/digest. This bounded control cannot dispatch inference.
4. The broker consumes the one-use approval while marking the effect dispatched
   before invoking `dots.effect.dispatch` on the registered stdio peer.

The server request includes the producer-owned immutable `identity`, proposal,
`content_json` and deadline. Validate the entire typed identity and resolve its
local mapping; do not substitute IDs from model arguments. `content_json` is the
exact canonical JSON string whose UTF-8 SHA256 and byte length are
`identity.content_sha256` and `identity.content_size`. Its parsed value must equal
the page document or computer input. This avoids cross-language serialization
ambiguity. No endpoint, environment variables or credentials can be supplied.

Requests are pinned to one transport object. Another peer, an unscoped answer,
or a reconnect replay cannot answer them. Executor requests are excluded from
`open_requests`; never treat a missed response as an instruction to execute again.

## Required native consumer guarantees

For pages, Dots must transactionally commit the expected-head CAS, immutable
version/canonical document digest and operation/effect-bound receipt in its one
native byte store. Reject changed bytes for an existing operation/effect.
Recheck page, parent, Space and stable-agent grants in that same boundary. The
producer serializes live project-grant changes with its native dispatch; Dots
must independently serialize its own grant changes at the actual write edge.
Owner manual edits remain explicit authenticated CAS operations and update native
immutable history/outbox. They do not mint an agent effect or move a producer
shadow byte store. Subsequent proposals observe the native head.

For computers, Dots must durably record intent/acceptance before forwarding to its
actual configured supervisor. Verify the stable-agent/container mapping, enabled
and browser/files/shell permissions, current grant/control revisions, and the
fresh snapshot ID/digest immediately before acting. Takeover/emergency stop and
revocation must fence queued dispatch and cancel running transport where
supported; cancellation after possible execution is `outcome_unknown`.
Only a fresh snapshot may restore control after human takeover. Preserve the
existing relative workspace path, sandbox, redirect, SSRF/DNS-rebinding and secret
redaction controls. Shell input is bounded to 60 seconds and never a generic
producer remote-shell route. Missing or unqualified infrastructure is unavailable.

Return only the typed `DotsEffectReceipt`. A committed receipt must echo the
entire unchanged identity, have a durable receipt ID, matching content digest and
a digest of the bounded/redacted native result. Page version must equal expected
head plus one. `not_applied` requires durable proof of rejection before execution,
not just a network error. Arbitrary provider messages are not receipts. Raw
supervisor output is not journaled; Dots owns redacted result retrieval by receipt.

## Recovery

A missing, malformed, foreign, late, or changed receipt leaves the effect
`outcome_unknown` or `reconciliation_required`. Terminal exact duplicate requests
return the original journal outcome without dispatch. Computer writes never
advertise provider idempotency; page CAS supports deduplication but the producer
still never blindly replays.

`runtime.dots.effect.reconcile` takes a bounded owner lease and sends only
`dots.effect.inspect`, containing the original immutable identity and deadline.
It must look up a durable native receipt/status without resending a mutation.
Preserve the original prepared generation and approval identity after restart.
No matching evidence means unresolved, not failure. Existing effect-list/get
methods expose native effect IDs and state for lost RPC-response recovery.

Focused Python tests exercise actual RPC, approvals, broker, journal, pinned
stdio callbacks and a separate native SQLite fixture. This is boundary evidence,
not qualification of Dots' actual page/supervisor pair, credentials, host sandbox,
network isolation or deployment. Those remain separate integration/live gates.

## Ordinary Ryoko model turns

The optional `dots_native` named toolset contains four concrete registry tools:
`dots_page_read`, `dots_page_propose`, `dots_computer_observe` and
`dots_computer_propose`. None is a core/default tool. The Dots BFF sends
`client.capabilities {server_requests: true, dots_native: true}` on its pinned
stdio connection **before** creating the agent. The gateway freezes that surface
when constructing the agent, adds the optional toolset, and intersects tool
exposure with the stable agent's immutable `allowed_tools`. Late advertisement,
adapter registration, or configuration changes cannot add schemas or mutate the
cached prompt of an existing agent; create a new authorized session instead.
Legacy/unbound identities and other transports cannot acquire these tools.

Advertisement is not permission. Configure the four individual tool grants as
needed, the page's project grant, and separate `dots_computer_<action>` grants
(e.g. `dots_computer_snapshot`, `dots_computer_click`). Register native adapters
before the first tool use. The authenticated BFF supplies authorized adapter,
project/Space/page and revision references in the bounded conversation context;
the model cannot turn a guessed reference into a grant. No second model loop or
unrestricted legacy Dots tool execution is used.

Page reads use the pinned typed `dots.page.read` callback and return one complete
immutable document version. Computer observations use `dots.computer.observe`
with an explicitly read-only action, or `result` for one already-confirmed native
computer effect. The producer verifies authority/scope echo, complete canonical
JSON bytes and SHA256, page version, and live grants after the read. Snapshot
observations bind their snapshot digest to those exact bytes. A computer result
read must match the result digest from the original confirmed effect receipt.
Returned content is explicitly untrusted source material. The native consumer
must retain its redaction, resource-size and supervisor isolation boundaries.

### Live-model exact reviews versus standalone RPC reviews

Model proposers run in the admitted ordinary `RuntimeRun`, prepare the same
broker-owned exact action and durable review record, then emit the typed pinned
server request `dots.approval`. Its parameters are:

- `session_id`: the **live gateway RPC session ID**, used for transport routing
- `authority.runtime_session_id`: the **durable logical runtime conversation
  ID**, which may differ from the live ID and survive reconnect/compression
- `authority.principal_id`, `profile_id`, `agent_id`, `run_id`, `policy_digest`
  and `generation`: the producer-owned stable actor and current runtime fence
- `approval_id`, `approval_digest`, `action_digest`, `expires_at`: the exact
  immutable durable human-review identity and deadline

The BFF must verify both session IDs through its stored live-to-durable mapping,
check the stable actor/run/generation and current pending JSON-RPC request ID,
and fetch `runtime.approval.get` for that live session and exact approval ID.
Render the complete immutable review, not a reconstructed description. If the
review is unavailable, do not infer approval from model or page text.

For this live-model path, answer the **original `srq-*` request ID once** with
`{approval_id, approval_digest, choice: "once" | "deny"}`. Echo both immutable
review fields exactly. Only the producer verifies and resolves the durable
record; the BFF does **not** also call `runtime.approval.resolve`. It inspects
`runtime.approval.get` afterward to observe pending/approved/denied/consumed state.
The existing standalone prepare/publish RPC path remains a separate decision
path and must not also answer a live-model request for the same action.

Another transport, an unscoped response, mismatched echo digest, unknown field,
broad `session`/`always` choice, or an expired/withdrawn request cannot resolve
this review. Review requests are excluded from reconnect replay. If the response
is lost, inspect the durable review; do not send a second resolve. A repeated
model request with the same `request_id` does not re-prompt an unresolved review.
Consumed proposals return their original effect state without repeating the
mutation; changing bytes under that request ID conflicts. Unknown effects use
the existing read-only reconciliation path. Unsupported server requests should
receive an explicit JSON-RPC error rather than a generic textual answer.

Model-turn tests use the real AIAgent loop, SDK HTTP serialization with a
simulated provider, actual registry/schema selection, durable review/broker and
pinned native SQLite peer. They verify frozen tool/prompt prefixes and policy,
read, approval, replay and receipt boundaries. Live host/supervisor qualification
remains a separate required gate.

Each physical native read/dispatch acquires its own bounded runtime executor
reservation and deadline when budget policy is configured. Human review time
uses the existing human-wait accounting and holds no executor slot. It cannot
increase the original runtime or approval deadline. Result uncertainty remains
an effect-journal concern; releasing the local request worker never authorizes
repeating a native mutation.

A model proposal's `request_id` is conversation-unique for a new intent and stable
for its retries. Operation/approval IDs derive from the durable conversation and
that request ID, so moving an uncertain proposal to a later model turn cannot
mint another execution. The original consumed effect is inspected unchanged;
new bytes under the same request ID conflict. A new intentional action requires
a new request ID and a new exact human review.

Live adapter registrations are scoped to the stable actor **and its durable
conversation**. Register each newly created/resumed conversation before use.
Opening another conversation for the same agent cannot move the first one's
review/read/effect callback routing. Reaped or detached conversations retain
journal evidence but lose live adapter authority; re-register before recovery.
Grant revocation must still fence the actual native store/supervisor globally,
regardless of any conversation's cached registration.
