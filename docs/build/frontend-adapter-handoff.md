# FE14: Hermes frontend adapter handoff

## Status

Producer-side handoff only. No source file in `ryoko-dots` was changed, no Dots adapter was installed, and no write-capable cutover or production readiness is established here. FE14's separate-consumer exit remains pending. Use the separate repository's `Integration_Handoff.md` when its implementation is authorized and available; this document does not claim to have validated that repository.

The current supported consumers remain the Hermes TUI, Electron renderer and web-hosted TUI. Their runtime controls are projections of the same Python-owned state and generated JSON-RPC contracts. [FE13's original receipt](fe13-validation.json), [additive consumer receipt](fe13-followup-validation.json) and [scope matrix](frontend-release-scope.md) distinguish tested local implementation from missing or unqualified capabilities.

Producer contract candidate: `b323e73f7b2f6792c57341240e206a72e43e3192`. Exact generated-file hashes and local document checks are in [FE14 receipt](fe14-validation.json). This pin identifies the producer only; no consumer commit or OD00 result exists.

## Authoritative inputs

- [Generated TypeScript wire](../../apps/shared/src/gateway-contract.generated.ts)
- [Generated OpenRPC schema](../../apps/shared/src/gateway-contract.openrpc.json)
- [Producer-only synthetic examples](release-contract-fixtures.json)
- [Backend release manifest](release-manifest.json) and [runbook](release-runbook.md)
- [Existing surface inventory](frontend-surface-inventory.md)
- [RuntimeControl consumer](../../apps/shared/src/runtime-control.ts) and its behavior tests

The JSON example file explicitly says `producer_only_synthetic_contract_examples`. Its absent Dots commit/OD00 result is an unresolved gate, not an approved compatibility certificate. Pin and hash the exact committed generated files when running a real consumer qualification. Do not infer compatibility merely from a schema number or a TypeScript build.

## OD00 read-only proof to run in the separate consumer

1. Resolve identity through the authenticated owning transport. Never accept principal, agent, profile, credentials or grants from a browser's intent JSON. Keep service authentication distinct from the human actor.
2. Negotiate `runtime.capabilities` and supported schema versions. An unavailable operation must render as unavailable, not silently fall back to a second agent loop.
3. Read `runtime.snapshot` for the exact owned live session. Preserve durable/lineage identity mappings separately from runtime IDs.
4. Apply `runtime.events.since` monotonically. Deduplicate event IDs/cursors; reject foreign session/generation events. On `snapshot_required`, replace the projection with the supplied authoritative snapshot before continuing replay. Missing history is visible, not invented.
5. Display accepted command receipts, mission execution, artifact verification, unresolved effects and delivery as separate facts. A transport finish event does not establish completion.
6. Test disconnect, restart, cursor expiry, same-account other-session denial, scope A→B→A, pending approval and failed delivery using an isolated synthetic home. Record exact producer and consumer commits and actual results.

This read-only proof needs no credential generation, automatic memory migration, new scheduler or external effects.

## Write-capable adapter prerequisites

A later Dots adapter must replace its inner generative/tool orchestration at the shared platform factories, rather than treating Hermes as another tool or merely redirecting a model base URL. Exactly one runtime owns each mission/effect/schedule. Native chat/page/task/review/voice/Slack surfaces may remain useful projections.

- Commands keep one stable `command_id`/idempotency key and the reviewed revision across an explicit retry after an unknown outcome. Changed intent gets a new identity. Never automatically rerun the mission to repair delivery.
- Approval binds exact bytes/target, live authority, owner generation and expiry. Editing content, changing scope or revoking grants invalidates review. A surface unable to provide secure exact-content review links to a supported authenticated review surface.
- Artifact versions/digests are immutable. Native page edits and canonical heads use expected-version conflict checks; one authoritative store is declared per artifact. Branch selection/merge is explicit and retains previous versions.
- Delivery acknowledgment follows complete validated bytes and actual text receipt/rendering. It is never human-read proof. Same-client monitor payloads require exact SHA256 over the immutable JSON string; terminal enqueue alone does not establish painted text.
- Each non-primary Dot maps to a stable specialist with isolated built-in memory. Primary personal memory never becomes shared team context. Legacy preferences require an explicitly selected supported destination; one-off corrections cannot silently become durable defaults.
- Scheduler migration happens once with stable import/occurrence identities, no overlap. Reconnect or a client request timeout must not recreate work. Voice hangup, speech stop, captured-audio discard and accepted-work cancellation remain separate.
- External channel/device mappings require verified identity, actual executor capability and real handoff evidence. Local JSON-RPC binding alone is not cross-device qualification.
- LAYA stays off/shadow until its point-specific backend, privacy, calibration and authorization gates are independently satisfied. Explanations reveal recorded decision facts, not hidden reasoning or new authority.

## Canary and rollback

Use a declared `runtime_owner` canary only after backend/FE13 mandatory gates and the separate consumer tests pass. Stop new admissions to roll back. Already accepted Hermes work retains Hermes ownership until resolved; never replay it through the old DotAgent loop. Keep mappings, journals, receipts and artifacts readable, including unresolved effects and partial deliveries.

## Required consumer receipt

Record producer commit, consumer commit, generated schema hashes, fixture identity, expected versus observed outcome, authority/identity checks, immutable artifact references, completion/delivery state, failures, user review/cleanup effort, latency/cost and explicit limits. A screenshot proves layout only. Do not mark OD01–OD04 complete from this handoff document or producer test counts.
