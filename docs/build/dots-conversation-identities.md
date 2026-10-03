# Registered agent conversation selection

Canonical conversation ingress still requires the server-owned launch-profile stdio transport. It rejects model-run invocation and caller-supplied principal/profile/home/policy fields. A selected `agent_id` is a lookup key; it never supplies identity or memory authority.

## API changes

- `runtime.conversation.create` accepts optional `agent_id`, `idempotency_key`, and optional `title`
- `runtime.conversation.list` accepts optional `agent_id` in addition to its existing page/search filters
- `runtime.conversation.operation.get` accepts optional `agent_id` alongside `idempotency_key`
- Every canonical conversation summary now includes the authoritative `agent_id`

Omitting `agent_id` retains the configured launch active agent. Only a configured primary owner may select a different registered stable identity. Specialist-owned launch contexts cannot select another agent. Ephemeral children and unknown IDs cannot be selected. The BFF should map its verified Dot ID to a producer-reported stable ID and check the returned summary; display names are never that mapping.

List cursors and durable operation receipts remain agent-scoped. The same idempotency key may identify separate operations for separate agents. A retry must retain the same target. Archived agents remain available for owned list and receipt recovery, but cannot admit new conversations; retrying an already committed creation returns its original receipt.

Bind, history, export, rename, and conversation archive do not accept a caller-selected agent. They read the stored immutable binding only after matching the configured principal, profile, and home and verifying the primary owner's ability to inspect the selected identity. A specialist cannot use a conversation ID to switch identities or inspect another specialist. Bind uses the recorded configuration snapshot, not the current launch default. Raw operator policy changes continue to invalidate mismatched bindings.

## Atomic enrollment and execution permission

Create atomically persists the session, immutable identity binding, selected configuration snapshot, canonical metadata, and idempotency receipt in the same SessionDB transaction. A configuration-head change during creation yields a revision conflict without leaving partial rows. A lost response is recovered through the existing target-scoped operation receipt.

Instructions and workflow knowledge remain frozen for each constructed session. Newly created top-level conversations enroll the current configuration; existing sessions cannot gain new grants or newly delivered prompt text through a rename, reconnect, or changed launch default.

Permission narrowing and agent archival take effect immediately through a persistent monotonic revocation floor checked at execution admission. They do not rewrite cached prompts or replace tool schemas. A later regrant does not revive an older session or its captured approvals: start a newly authorized conversation. Owner-authorized historical transcript reads remain available. The exact owned primary settings RPC can repair settings without granting an exception to execution or approvals.

## Display titles are metadata

Canonical `runtime_conversations.display_title` is independent of the legacy globally unique `sessions.title` alias. Multiple conversations, including conversations belonging to different specialists, may all be called “A new thought.” Creation leaves the physical alias unset; rename compares the canonical metadata revision and changes only the canonical display title.

Search, pagination, restart, and compression retain that canonical title. Upgrade backfills existing canonical rows from their prior physical session alias, including an alias moved to a compression continuation. Noncanonical sessions retain their existing title behavior and uniqueness rules. Historical idempotency receipts retain their original title/revision, while current list and bind responses expose current metadata.

## Active versus desired session knowledge

`runtime.agent.session.get` takes the owned live `session_id` and `schema_version: 1`. It returns active and desired configuration revisions, whether startup knowledge is already frozen, current managed-authority status, and active/desired workflow pins with immutable version/digest/delivery revision and current workflow lifecycle state. It returns no instructions, memory, transcript or workflow body. A revoked session remains inspectable through this owned read-only projection.

The active list is the exact persisted startup snapshot; delivery or rollback changes only the desired list until another session is constructed. A revoked workflow can remain in frozen advisory text while its lifecycle state is explicitly revoked and execution stays denied by the existing workflow controls. This metadata endpoint grants no execution authority.
