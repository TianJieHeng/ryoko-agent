# Agent-owned memory and context recovery

BE08 routes each strict identity to exactly one backend. The configured primary uses personal MCP only. Stable specialists and ephemeral children use individual built-in namespaces. The identity binding, not a display name or model instruction, selects the route.

## Individual built-in records

The existing MemoryStore interface is backed by an owner-bound structured catalog for strict built-in agents. Namespace identity includes principal, profile, agent, home and lifecycle. Each namespace has one authoritative SQLite record catalog; MEMORY.md and USER.md are validated derived projections. Neither an ambient profile switch nor a new child can repoint a live store. Primary construction is refused. Legacy identity-absent stores keep their established behavior.

Records retain ID, owner, kind, source/author, creation/update and validity times, version, confidence/validity, scope, supersession and deletion state. Writes use exact expected-version CAS. Conflicts remain explicit, rather than overwriting another correction. Tombstones disappear from recall/projections/normal exports and redact historical content; this does not promise physical erasure of the catalog, filesystem backups or previously exported copies. Procedure references must name a positive canonical workflow version and remain uncertain until the workflow authority can resolve them.

Storage is bounded: active character budgets, 1,024 heads, 4,096 versions, 256 conflicts and a 32 MiB mutation ceiling. The first certified checked-file implementation requires Linux dirfd/flock/proc semantics and fails closed on unsupported hosts. Symlinks, hardlinks, foreign ownership, unsafe permissions and external projection drift refuse. A crash after catalog commit or partial projection write is recoverable without treating Markdown as a second authority.

Ephemeral children keep isolated retained namespaces across supported resume. Close does not erase memory. Retention/archive state is explicit; automatic deletion and a new semantic index are not enabled. Stable specialists never inherit unrelated child IDs.

## Reads, corrections and explicit project scope

The ordinary system-prefix snapshot stays frozen for the conversation. Structured corrections/tombstones arrive through a bounded fresh current-turn sidecar, carrying record versions and invalidations. The cursor advances only after the exact sidecar bytes persist. Prior message bytes are not rewritten to smuggle new memory into an old prompt.

Individual context is the default. Project-scoped notes require immutable project policy IDs plus live project grants and are excluded from the global prefix/default recall. `runtime.memory.scope.set` explicitly selects a granted project for this session's fresh context; it never infers one from cwd. Each scope has its own bounded cursor/ack state, so switching projects cannot silently lose earlier applicable corrections. Primary personal MCP does not invent a project namespace mapping.

Owned memory RPCs expose status, structured records, CAS write/tombstone, bounded listing and revision-bound local JSON export. Clients supply stable record IDs. Paged/export continuations require the original revision; a changed snapshot returns a conflict, not mixed bytes. Exports use full digest/size and at most 65,536 raw bytes per chunk, with an 8 MiB serialized bound. This is an authenticated local download, not external sharing or a remote-harness export claim.

Existing lexical/session recall filters principal/profile/agent/home/lifecycle before SQL limits, snippets, title matches, browse, direct IDs, scroll anchors and lineage hydration. A child's lineage is not permission to read its parent's private transcript. The primary personal backend cannot use local session recall as a replacement personal index. Certified built-in memory and scoped raw session reads use the existing wall/executor budget reservations; strict generated-summary search remains explicitly unsupported. Strict shared pending-write review and background-review forks remain disabled until they have equivalent ownership.

## Personal MCP adapter and operator qualification

The generic context-pack adapter uses the already-certified MCP registry/broker/transport. It does not add a second HTTP client, bypass the tool/credential/egress policy or embed a deployment endpoint/token. The field-by-field configuration contract is in [memory-backend-contract.md](memory-backend-contract.md). An operator supplies exact server/tool schema pins, primary-only credential reference and egress grants, bounded argument names, response mapping and an explicit local JSON response schema. Remote schema references are rejected. Config-ready health remains `live_unverified` until a validated recall.

The supplied operator documentation describes stateless Streamable HTTP, bearer door keys, one downstream primary principal, context-pack recall, immutable lineage revisions, trust-gated supersession and retractions/tombstones. It does not provide complete machine-checkable mutation/correction/delete/export or uncertain-outcome contracts. Abbreviated signatures are not substituted for schemas. Live acceptance remains operator-gated; no attached credential has been used or placed in this repository.

The client credential reference named by the operator manual is `HERMES_MCP_TOKEN`; its value must be configured locally through the operator's secure setup. `HARNESS_KEY` is server-side downstream authentication and must not be substituted for the client token. Local environment files must remain ignored. Do not place real values in tracked examples, prompts, logs or journals.

Recall retains the whole bounded context pack, including relevance, usage and provenance labels, as untrusted data. Those labels are not execution permission. Errors, malformed/partial/oversize packs, scope changes or unavailable transport become explicit degraded context, never an empty success or hidden local personal store. Confirm missing context with the user; already authorized active context/project artifacts remain available. Strict MCP with enabled runtime budgets remains unsupported until a charge adapter exists.

Remote writes, supersession, delete, export and ingestion remain visibly unsupported without verified semantics. The adapter does not queue private writes, retry ambiguous mutations or fan out to disabled providers. No disabled provider receives startup, recall, sync, compression, session-end or teardown payloads. API manager reuse and teardown bind the exact privacy owner/configuration/endpoint/credential scope.

## Transactional compaction and rollback

ContextProjection and runtime checkpoint metadata commit with the existing archive-and-compact transcript rewrite, concurrent-tail preservation and sanctioned new prefix. Generation/revision/source-byte fences reject stale or torn projections. Strict identity-bound compression currently certifies in-place replacement only; session-rotation compression refuses before summary, while legacy rotation remains unchanged. Exact protected evidence/approval references and native provider sidecars remain recoverable. Private provider cache keys include authenticated identity/privacy, endpoint and credential scope; matching content does not grant reuse.

Preserve namespace metadata, structured history/tombstones, source archives, projection/checkpoint readers and consumed approvals on rollback. Never copy primary harness content into a shared built-in store. Any later backend migration, physical erasure, hybrid index, provider federation or live mutation adapter requires its own authority and validation.
