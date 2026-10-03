# Canonical conversation display titles

Canonical display names live in `runtime_conversations.display_title`, independently of the legacy globally unique `sessions.title` alias. Repeated names, including the frontend default “A new thought,” are supported. Display names do not select identity, ownership, or a memory namespace.

Creation stores the requested display name atomically with the conversation and durable request receipt, leaving the physical session alias unset. Rename changes canonical display metadata under its existing revision check. It never assigns one globally unique alias to every physical session in a compression lineage. List/search and bind use the canonical title; idempotency receipts retain their original revision and title.

The additive column reconciler installs the column on existing stores. An idempotent backfill retains the previous session title, including an alias transferred to a compression continuation. Existing noncanonical sessions and their title uniqueness behavior are unchanged. The backfill never overwrites an initialized canonical title on restart.

No RPC request or response shape changes. Validation covers repeated titled and untitled creation, compression rename, search and pagination, owned profile isolation, restart/retry, and upgrade from a prior canonical conversation schema.
