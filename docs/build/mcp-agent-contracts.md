# BE05 agent-owned MCP contracts

This is an opt-in strict-identity HTTP boundary, not a deployment of the user's
personal memory harness. No real server identity, endpoint, credential or live
schema digest is supplied by this implementation.

## Explicit admission and ownership

An enabled agent identity needs both the existing exact `mcp_grants` operation
allowlist and an `mcp_policies` entry for the server. The model-facing tool name
must also be in `allowed_tools`. Personal-server and personal-credential
inventories remain primary-only. The same-profile specialist and ephemeral child
cannot obtain their authority through tool search, cached manifests, direct
utility handlers or a parent's pool.

`mcp_policies[server]` accepts:

- `policy_version`: positive integer changed when the operator reauthorizes
- `transport`: currently only `streamable_http`
- `endpoint`: exact configured credential-free HTTP(S) base endpoint
- `tool_allowlist`: exact raw MCP tool names, also present in `mcp_grants`
- `schema_digests`: each callable raw tool's operator-approved SHA-256 digest
- `read_scopes`: exact `resources/read` URI and `prompts/get` name lists, plus
  explicit booleans for `resources/list` and `prompts/list`
- `secret_ref`: optional exact granted bearer credential reference
- `read_only_tools`: exact operator-declared read-only tools within the allowlist
- `sampling_limits`: currently only `{"enabled": false}` is supported

A missing tool pin may be diagnosed by discovery, but it cannot register or invoke
that tool. Pins cover the complete SDK Tool JSON record, using SDK JSON aliases
and omitted nulls, canonical sorted compact JSON and SHA-256. The legacy SDK
fallback covers name, description, input/output schemas, title, annotations and
metadata. Operators must obtain and approve actual server schemas; neither a
server's description nor a first-connect hash automatically authorizes them.
This requirement survives process restart without adding a trust-on-first-use
ledger. Changed schemas need changed trusted policy and a new authorized context.

The separate `recipient_plan` must explicitly grant purpose `mcp`, the same server
identifier and endpoint, and the certified HTTP transport. Ordinary config and
credential availability alone do not supply an egress grant. Connection and tool
registry namespaces bind profile, principal, agent, identity configuration,
policy, current configured MCP routes and granted credential hashes. They do not
contain plaintext credentials. Legacy profile cache manifests are neither read
nor written as strict agent authority. Empty new fields are omitted from policy
serialization, preserving earlier empty-policy digests.

## Certified and unsupported routes

Strict HTTP uses a caller-owned SDK client, disabled environment proxies, exact
endpoint/bearer binding, and recipient request checks on every actual redirect or
retry. Explicit bearer authentication is the only certified credential shape;
profile live-endpoint credentials, arbitrary custom authentication headers,
OAuth, SSE, client certificates and insecure TLS overrides remain unsupported.
Strict stdio is unsupported because the available computation-only executor
cannot certify arbitrary MCP server subprocesses. No local directory or filtered
environment is described as a sandbox.

MCP is unsupported whenever `runtime_budget` is configured. This includes startup
handshakes, metadata discovery and direct prompt/resource reads before an
initiating run exists, because the BE03 adapter does not account for those
potentially chargeable requests. Unbudgeted strict connections report cost as
untracked. Legacy sampling and modern SDK MRTR callbacks reject strict requests
before any auxiliary model call; enabled sampling-limit configuration is rejected
rather than accepted as unenforced model/prompt/output/depth/concurrency limits.

## Dispatch, refresh and teardown

Every real invocation rechecks live policy, exact target, owned connection and
live durable run. Direct handlers consume the same exact capability as registry
dispatch. Mutations require a one-use approval bound to endpoint, server, raw
operation, argument bytes, schema/handler contract, agent and run generation.
Only an operator read-only grant can classify a read; `readOnlyHint` supplies
neither authority nor retry safety.

Tool refresh checks pins before replacing the authorized registry. Removed or
changed contracts cannot be restored by stale handler references or same-code
conversation tool pins. Prompt/resource refresh notifications produce explicit
unsupported/reauthorization-required status and withdraw the old callable
registration. A new authorized context is required; notifications do not silently
rewrite a conversation's frozen prompt prefix.

Potentially mutating transport loss retains `outcome_uncertain` and is never
blindly replayed. Existing read-aware reconnect behavior uses the operator
read-only contract. Legacy deployments may declare exact read-only names through
`mcp_servers[server].tools.read_only`; remote hints alone no longer waive approval
or permit session-loss replay.

Ephemeral child teardown closes only that child's agent-owned local connections,
including stale policy namespaces. Strict SDK transport teardown disables remote
session DELETE, so closing local sockets does not create an unapproved network
operation or claim remote cleanup/cancellation. Remote cleanup remains
`unconfirmed`. Stable primary pools remain connection-lived. Revoked policy cannot
reconnect or restore cached authority.

## Validation scope

Focused fixtures exercise real policy parsing, ContextVars, registry dispatch,
SQLite durable owner fences, exact approval consumption, SDK callback routing and
mock HTTP redirects. Credentials and endpoints in tests are harmless fixtures.
There is no live-harness, billing, hardware or remote-cancellation certification.
