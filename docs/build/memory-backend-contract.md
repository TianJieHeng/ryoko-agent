# BE08 per-agent memory boundary

Strict identity profiles route the configured primary only to `personal_mcp`.
Specialists and task children use their own immutable identity-bound built-in
catalog. Missing personal configuration produces an explicit degraded notice,
without constructing a built-in personal store, installing a plugin, retaining a
private write queue, or copying private recall into an auxiliary semantic index.
Legacy profiles retain their existing opt-in provider behavior.

## Personal MCP configuration prerequisites

The generic `MCPContextPackProvider` uses the existing certified MCP registry and
capability broker. It does not install a vendor plugin or maintain another
transport/client. Before enabling recall, the operator must supply:

- Exact server identity, Streamable HTTP endpoint, and primary-only policy grant
- Exact approved tool input/output metadata SHA-256 pin, tool allowlist and explicit
  read-only classification. Untrusted `readOnlyHint` is insufficient
- An endpoint-bound bearer `secret_ref`, present only in the primary credential
  inventory, and an explicit recipient-plan grant with purpose `mcp`
- A complete `memory.personal_mcp` context-pack contract, described below

The supplied harness documents name `HERMES_MCP_TOKEN` as the client credential
reference. `HARNESS_KEY` is server-only. No real token or private endpoint is
included here, and no real credential was configured or transmitted for BE08.
The local configuration pattern is an ignored profile `.env` entry such as
`HERMES_MCP_TOKEN=<operator-supplied-value>` and an MCP header reference
`Authorization: Bearer ${HERMES_MCP_TOKEN}`. This is a placeholder, not a command to
install credentials; only the authorized operator should perform that setup.
Never put token bytes in tracked YAML, examples, tests, prompts, or logs.

The identity policy's `secret_ref`, personal credential inventory and granted
`secret_refs` must agree. Credential resolution is through the current owning
profile's scoped secret reader, never an ambient fallback. The exact endpoint and
bearer are checked again by the certified HTTP layer before transmission.

`memory.personal_mcp` requires all these fields:

| Field | Meaning |
|---|---|
| `server`, `tool` | Exact granted MCP names |
| `contract_version` | Operator-supplied contract identifier, at most 128 characters |
| `scope` | `primary_principal`; all runtime keys share this personal principal |
| `format` | `context_pack`, never an assumed list of independent memories |
| `response_field` | `result` or `structuredContent` in the certified handler envelope |
| `response_encoding` | `object` or `json` according to the actual server response |
| `response_schema` | Exact, self-contained object JSON Schema; all schema references are rejected |
| `query_argument` | Actual query argument name; supplied docs describe `query` |
| `budget_argument` | Actual token bound argument; supplied docs describe `token_budget` |
| `links_argument` | Actual link option argument; supplied docs describe `include_links` |
| `token_budget` | Integer 1–8000 |
| `max_chars` | Complete returned pack bound, integer 256–32768 |

The actual input and result JSON schemas were not supplied/verified. The docs'
parameter shorthand is not a schema; the synthetic test schema is not a deployment
contract. Until the operator supplies and verifies these exact contracts, leave
`memory.personal_mcp` absent. Default health is `unconfigured`. Locally validated
configuration is marked `live_unverified` until a schema-valid recall succeeds.

The adapter preserves the entire bounded context pack, including relevance bands,
usage hints and provenance present in the declared schema. It treats them as
untrusted context. A `safe_to_act` label never authorizes actions. Unsupported
freshness/version guarantees remain unverified. Partial, malformed, error and
oversized responses produce degraded context. The response is never spilled to a
separate local memory file. Ordinary authorized session sidecars retain the exact
model-facing context under the existing conversation-retention rules.

Only recall is supported. Remember/intake, supersession, correction writes,
forget/delete, export, session ingestion and invalidation subscriptions remain
unsupported without actual semantic receipts, idempotency and effect-reconciliation
contracts. A changes sequence is not an export. No mutation is automatically
retried. Strict stdio, OAuth, SSE, sampling and budget-enabled MCP remain unsupported
by the existing certified transport/runtime boundary.

## Built-in applicability and continuity

Stable specialists and persisted task children retain separate structured catalogs,
CAS record versions, conflicts, tombstones and validated human-readable projections.
Frozen startup memory never changes the system prefix. Fresh record corrections
enter only the current user sidecar and are acknowledged after durable persistence.
Restart conservatively replays fresh state rather than trusting an in-memory cursor.

Project memory requires explicit owned `runtime.memory.scope.set`, a live read
grant, and a separate namespace/scope cursor. No cwd or remembered preference
selects a project. Selection is session-local and is not persisted across restart.
Switching back to a prior project resumes its cursor; revoked access stops reads.
The default scope is individual-only. Primary personal memory does not invent a
project namespace or expose built-in record controls.

Managers and API-server pools bind immutable identity, home, policy, configuration,
backend, endpoint and credential scope. Cross-owner reuse/teardown is rejected;
disabled and unsupported provider operations receive no private lifecycle payload.
