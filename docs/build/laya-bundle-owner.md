# Qualified request-schema bundle ownership

The DP16 owner is implemented behind release qualification. Production config
still rejects enforcement. Synthetic trusted-host fixtures test the owner; they
do not constitute an approved deployed release. [Runtime qualification remains
blocked](laya-runtime-qualification.md).

## Real ownership seams

1. `turn_context.build_turn_context` captures durable first-freeze evidence after
   admitted turn identity and before the first system/tool persistence
2. After turn-start compression, `prepare_turn_owner` prepares the exact current
   request through the same bounded planner used by the late observer
3. `turn_request_assembly.assemble_api_request` selects undecorated request schemas
   from the owner before cache decoration. Canonical discovery stays full
4. `_finish_compaction_boundary` notifies the owner only after actual durable
   success; a prompt rebuild, attempted refresh, new task or new object is not a
   cache boundary. The owner additionally requires the current committed context
   projection/checkpoint witness
5. Provider retries, ordinary turns, MCP refresh and fresh-agent resume use the
   same frozen schema bytes. Tool search/describe/call recovers omissions as data;
   dispatch still checks current grants and confirmation requirements

## Qualification and persisted evidence

A trusted host must supply an `OwnerQualification` with a real `PointPolicyBook`,
an exact expected model/calibration/service bundle and a bound client factory.
There is no config boolean, classifier output or chat-controlled deserializer for
this capability. The policy book verifies the explicit DP16 v2 contract plus
renderer/catalog/evaluation fingerprints and independently authorized release
reports. Restart creates no new release authority.

Installation reloads the exact durable `decision.tool_plan` and every receipt from
the same admitted run, validates their digests, pins, thresholds, scope and complete
causal plan, then composes the deterministic planner floor through the policy book.
A modified shortlist, missing receipt, v1 release, old turn/generation, changed
catalog or wrong release preserves the incumbent.

The existing session `model_config` owns a separate `decision_request_bundle` pin.
Its fenced transaction rechecks active command/identity/lease/cancellation, commits
exact schema JSON/digest, context/boundary witness and release/receipt references,
and appends closed `decision.bundle` metadata atomically. No competing registry or
store is introduced. Schema contents and private context are not public event data.

A separate request pin is necessary because normal canonical MCP restoration may
merge newly discovered schemas. Applying that merge to an already-sent reduced
prefix would silently invalidate its cache. Missing or unverified historical
state never grants a new first-freeze boundary.

## Failure, restoration and rollback

- Off/unqualified requests do not classify, reduce schemas or apply planner-only
  input limits to the main provider
- A confirmed lack of a pin uses incumbent schemas. Failed restoration of a
  potentially existing prefix must recover the exact pin or defer dispatch;
  silently expanding schemas is not a safe ordinary-turn fallback
- A valid in-memory frozen prefix remains unchanged across transient read errors
- Cancellation, stale receipts or late results cannot install a new bundle
- Permission revocation blocks execution immediately even while the old frozen
  schema remains visible; schema visibility never grants invocation
- Rollback stops new effects and retains same-context bridge recovery. At a real
  committed boundary, restore complete incumbent schemas. Never rewrite a live
  cached prefix merely because a policy switched off
- Native Codex/detached compaction and legacy rotation without the required
  durable projection witness remain non-enforcing; no synthetic success claim
  substitutes for qualifying those owner paths

## Bounded journal and accounting

Optional decision, policy and tool-plan writes use the shared bounded receipt
pool. No lifecycle control lock spans storage I/O. Original deadlines fence the
transaction before and after insertion; timed-out precommit work rolls back.
After the final guard, an unacknowledged commit is unknown and blocks dispatch
rather than pretending no durable effect occurred. The final in-memory plan
publication uses only a brief deadline-bounded control lock.

A genuine SQLite transaction or filesystem commit stall can still obstruct other
users of the shared store; this removes the additional lifecycle-lock obstruction,
not the storage engine's limits. Worker capacity remains occupied until actual
exit. Transport uncertainty is snapshotted per submitted batch before admitting
the next caller, so a different caller's quarantine cannot retain this operation's
budget reservation.

## Local evidence scope

Owner tests exercise actual admitted AIAgent requests, durable first system/tool
persistence, three-stage qualified fixture receipts, retries/cache decoration,
full-prefix equality on follow-up and fresh-agent resume, real bridge dispatch,
MCP discovery expansion, successful/failed committed compression, stale binding
and persistence failures, rollback, revocation and A→B→A identities/profiles.
Focused cache/compression, policy and generated-contract tests also run locally.

These are code invariants. Real model quality, latency, cost savings, private-data
qualification and operator-approved deployment remain separate release gates.
