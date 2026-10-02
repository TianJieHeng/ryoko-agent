# BE01 identity runtime boundary

The optional, versioned `agent_identity` configuration is a trusted operator policy.
See the [generated field reference](agent-identity-reference.md). No actual user IDs,
server names, tokens or deployment configuration are installed by this change.

## Implemented path

`AIAgent`'s common constructor resolves immutable policy before provider routing or tool
loading. Existing CLI, TUI, messaging gateway, API server and cron construction converge
on this path. The existing `TurnContext` carries the binding; it is not a second turn loop.
`AIAgent.runtime_context` is read-only. The common turn and resource/memory teardown
methods bind profile home, stamped secrets, terminal policy and identity together.
Context-aware thread helpers preserve this scope; an unbound strict-profile dispatch is
refused rather than treated as primary.

A new session gets the configured active stable identity. Configured primary and specialist
IDs do not come from user names, prompts, platform labels or renderer claims. A delegated
child requires its parent's current binding and a child-policy ceiling; it gets an ephemeral
ID and intersected grants. Background cache-parity forks are refused under strict policy
until isolated memory routing replaces their inherited shared-store behavior. The standalone
curator likewise reports that an isolated background owner is required.

Existing empty surface-created session placeholders may acquire a binding once. The existing
SessionDB transaction checks the record and transcript: competing bindings conflict, and an
unbound session with historical messages requires explicit migration. The binding is recorded
before provider setup. A policy/config/home mismatch refuses resume. Removing the policy
cannot turn an already bound stored session into a legacy unrestricted session.

## Enforcement, not only schema visibility

- Non-global secret reads require an explicitly granted reference and a matching stamped
  scope. Missing keys do not use ambient process values or caller-supplied defaults
- Child environment filtering runs after raw, explicit, managed and served-profile overlays.
  Denied credential-shaped names, known credential names, all configured reference names,
  personal references and stale dotenv/source residue are removed. A granted value comes
  from its authoritative scope, not an attacker-supplied environment override
- Direct provider credentials passed to construction must match granted scoped values.
  Opaque externally supplied credential pools are refused until identity-aware provider
  bindings exist; names or inherited pool objects are not authority
- Schema filtering, tool-search/bridge dispatch, registry dispatch, inline tools and hook
  boundaries apply grants. Replacing a tool schema or cached list does not grant invocation
- MCP configuration is filtered before interpolation/admission. Fresh connections carry
  policy ownership; direct tool and resource/prompt utility operations, reconnect and shared
  connection adoption recheck it. A strict-profile boot without trusted identity defers
  agent-owned discovery

## Explicit phase boundaries

This is application-level authority enforcement, not hostile-code or hostile-tenant
confinement. Strict nonprimary identities currently expose only granted safe session-control
routes. Their generic file/shell/code/browser/plugin and MCP execution remain explicitly
unsupported until BE05 certifies the executor and agent-owned connection pools. No operating
system security or network settings were changed.

Per-agent automatic memory is unavailable until BE08. Strict construction does not load the
profile-wide built-in store, even if the memory toolset is explicitly selected, and does not
start an external automatic memory provider. There is no hidden built-in personal fallback.
Configured primary MCP tools may be used only through their explicit admitted grants; that
is not a claim that an unknown personal harness supports recall/write/delete operations.

Nonempty project or purpose-egress policy fields are rejected until their owning enforcement
arrives in BE07/BE05. They are not accepted as inert security knobs. Provider wire/endpoint
capabilities, persistent specialist memory, complete schedule ownership and hardware/live
service proof remain the owning later phases. Existing profiles with an absent or empty
policy retain legacy behavior and do not gain new isolation guarantees.

## Inspection and rollout

`hermes config identity` prints redacted effective policy. `--agent <configured-id>` inspects
a stable policy; `--session <session-id>` reads the existing profile database in read-only mode
and checks its stored binding. It does not initialize a database or call a provider. Credential
values and reference names are omitted. Effective configuration preserves the established
user expansion and managed-overlay order; invalid policy is never silently treated as absent.

Start with a fresh test profile and synthetic references. Existing transcript adoption is a
separate migration, not an automatic privilege change. A changed policy takes effect for new
sessions; stale stored sessions require a reviewed migration or a new session. Keep the
previous compatible code/config pair for rollback. A downgrade must not remove bindings or
reopen a bound session without enforcement.
