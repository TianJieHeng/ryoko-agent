# Dots owner-scoped identity configuration

The `runtime.agent.*` version-one RPC family uses an owned, profile-bound **primary** session. Specialists cannot manage this registry, and mutations cannot run from an active model-run scope. The existing personal-memory harness remains unchanged; these methods do not claim personal-memory mutation or erasure support.

## Wire methods

All requests include `schema_version: 1` and the owned live `session_id`.

- `runtime.agent.list` returns `agents` and `activation: "next_session"`
- `runtime.agent.get` takes `agent_id`
- `runtime.agent.create` takes `config` and optional `copy_from_agent_id`; the producer issues the new stable specialist ID
- `runtime.agent.update` takes `agent_id`, `expected_revision`, and the complete replacement `config`
- `runtime.agent.archive` takes `agent_id` and `expected_revision`; it retains history and memory, and never archives the primary

A mutable `config` contains `name`, `instructions`, `research_allowed`, `memory_allowed`, `project_grants`, and `default_project_id`. Role, backend, ID, credential references, MCP policy, and namespace are not accepted inputs. Project IDs must remain inside the configured source ceiling; default project must be explicitly included. Research and memory switches narrow the ceiling. They cannot install tools, credentials or providers.

A returned agent record exposes its stable ID, immutable `role`/`memory_backend`, `builtin_memory_namespace` (null for primary), revision, archived state, editable config, and `active_session_revision`. That last field describes the calling owner's frozen configuration snapshot, not a claim that every live specialist has been restarted. The response declares next-session activation for frozen instructions and grant expansion. `authority_revocation_revision` exposes the persistent permission-revocation floor; `active_session_revision_revoked` says whether that calling snapshot is already fenced. The frontend should show pending settings when the desired revision differs from the calling session's snapshot, and must not describe a revoked snapshot as still authorized.

Creating without a copy source uses only the configured child policy intersected with the configured primary ceiling, then assigns specialist/built-in authority. It fails explicitly if that ceiling is unconfigured. Copying a primary is rejected. Copying a specialist never copies its memory or live project ACL; its grants cannot exceed the copied configuration. The new identity receives a distinct built-in namespace. Project grants are still intersected with the existing live project ACL at access time.

## Session lifecycle and cache contract

The canonical profile state.db owns immutable configuration snapshots and session enrollment. The constructor binds a snapshot before creating tools, memory or a cached prompt. Existing sessions retain their enrolled instructions and tool schemas, including after process restart. Grants never broaden in place; permission narrowing or archival immediately fences effects through a persistent revocation floor without rewriting those cached instructions. Later regrant does not revive old sessions or captured approvals. Descendant construction retains its parent's frozen configuration ceiling; create a new top-level conversation to activate newly edited identities. Workflow delivery has its own immutable startup enrollment described in the delivery contract.

The trusted adapter may use `configured_agent_selection(agent_id)` when constructing a new top-level session. It supplies only a registered stable ID and requires the canonical durable profile state store. Legacy alternate database paths retain their original behavior but cannot enroll managed identities. The producer resolves the role, namespace and all effective policy. A resumed conversation cannot change its selected identity. Arbitrary caller-provided identities are never adopted.

The operator's actual `agent_identity` configuration remains the authority ceiling and live revocation source. Changing that ceiling invalidates managed snapshots instead of silently retaining revoked permissions. Managed edits do not rewrite config.yaml, reload personal memory, mutate existing system prefixes, or reinterpret display names as authority.

Archiving stops new enrollment and fences further effects from existing contexts. It is not a destructive memory-delete operation and cannot undo an effect already dispatched. The memory RPCs continue to require their actual initialized backend capability. Personal-harness operations remain unsupported wherever no real harness protocol exists.
