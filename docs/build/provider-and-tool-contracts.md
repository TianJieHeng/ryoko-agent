# Provider attempts, protocol fidelity and authorized tool views

BE04 builds on the identity, owner-generation and budget contracts. Provider
capability declarations describe recognized adapters, not a promise that every
model supports every feature. They do not create credentials or authorize a new
recipient. `runtime.capabilities` publishes safe declarations and the already
stored tool view without probing services or rebuilding the prompt.

## Provider ownership and replay

Recognized adapters declare streaming, parallel tools, media, usage, cache
semantics, cancellation semantics, opaque-state version and execution ownership.
External agent/native inner loops remain explicitly provider-owned and unsupported
for durable execution until their contracts can be enforced. Unknown client
implementations cannot become trusted merely by claiming a familiar API mode.
Model-specific capabilities remain unverified. A local interrupt is not upstream
cancellation acknowledgment. The bounded cost adapter remains conditional on the
operator-verified BE03 text-only OpenAI request contract.

Schema 34 adds a nullable, versioned private provider sidecar for native ordered
blocks that previously lacked an authoritative persistence field. Existing signed
reasoning and Codex continuation arrays retain their original columns. Canonical
replay and transcript repair restore supported sidecars; unknown versions fail
explicitly before dispatch. Display projections omit private storage envelopes;
authorized portability retains replay state. Multipart media follows the existing
JSON content representation instead of becoming an irreversible screenshot label.
No service is contacted to fetch or validate media as part of persistence.

## Finite physical requests

The certified BE03 request adapter attributes each physical attempt to its existing
budget reservation. Safe attempt metadata contains an opaque account reference,
logical request ID, attempt/reservation ID, reason and remote-acceptance state.
Raw endpoint, credential, request and response bodies are not included in the
runtime event projection. The SQLite budget writer remains the sole spending
authority; attempt policy is not another allowance.

Auth, exhausted quota, throttling, overload, context overflow, unsupported
capability, explicit refusal and ambiguous transport failures are distinct.
Bounded attempt and account-circuit policy prevents retries from entering the
independent legacy refresh, fallback and post-exhaustion ladders. Account circuits
are bounded process-local cooldowns; durable spending and uncertainty remain in
BE03 across process restart. Only evidenced refusals may retry; a 503 or timeout
with unknown remote acceptance stops conservatively. Retry-After is
a floor; if the delay does not fit the deadline/policy, the request stops rather
than retrying early. SDK retries remain disabled. Context overflow is terminal in
the first bounded policy, rather than masquerading as throttling or silently
rewriting preserved opaque protocol state.

Unknown usage remains conservatively reserved, including HTTP refusals without a
usage receipt: a 4xx status alone does not certify zero billing. A completed
refusal can release its provider slot while unknown token/cost maxima stay held.
A missing response, closed local
socket or failed callback cannot refund a remote operation or certify its end.
Attempt/circuit enforcement is certified only for the supported budget adapter;
unmetered legacy paths do not gain that certification by importing these helpers.

## Recipient authority and client reuse

Configured strict identities do not automatically authorize provider or model
fallback, even with budgets disabled. An endpoint/model list or a usable key is
not permission to transmit private context to another recipient. Automatic
strict fallback is refused until recipient/purpose grants and compatible
protocol/tool continuation can be proved. Main and auxiliary fallback paths share
this boundary; discovery cannot evade it.

Reused clients and underlying transports include identity/configuration/secret
scope and destination alongside applicable proxy, TLS and event-loop ownership.
Opaque account correlations do not expose raw secret material. Existing client
abort/release ownership is preserved; cancellation must not close another task's
socket or reopen a poisoned transport.

## Tool exposure and discovery

The shared registry provides immutable catalog metadata; each session derives its
own immutable installed, authorized, discoverable and selected view. Catalog
versions hash only authorized metadata. Unavailable reasons use policy-safe codes,
and denied MCP registration names never enter search or availability summaries.
Process-cached service availability is not a session authorization decision.

Construction and already-permitted schema-refresh boundaries capture a view. The
final selected set follows actual one-shot/child pruning and atomic schema
publication. Reading capabilities only inspects that saved object; it cannot
reload configuration, refresh MCP, change tools or invalidate a stable prompt
prefix. Prefix restoration updates the selected projection without new probes.

Exposure is not authority: direct calls, bridge search/describe/call and reopened
schemas still enforce the same live BE01 grants. Primary personal MCP remains
unreachable to specialists and children. No LAYA filtering or new cross-agent
memory sharing is enabled by this phase.

## Rollout and limits

No live provider, model, price, credential or sandbox capability is configured.
Preserve compatible sidecar readers and immutable identity/budget history during
rollback; do not discard native state to make an incompatible route appear to
work. Focused deterministic protocol, request, cache, ownership and discovery
checks are recorded in the phase journal; they are separate from live provider,
billing, hardware and release certification.
