# Ryoko Backend Build Plan

This plan chronicles the full runtime upgrade, its dependencies, implementation seams and evidence required to finish each phase. Use it with [FrontEnd_BuildPlan.md](FrontEnd_BuildPlan.md) and the shared [buildjournal.md](buildjournal.md).

**Role:** implementation roadmap. Current completed checkpoints, exact commits, validation and remaining limits are recorded in [buildjournal.md](buildjournal.md). The original planning-only status is historical; this roadmap itself is not execution evidence.

**Current Git publication rule:** publish verified phase commits directly to `main`, with the journal and a user update after each phase. The user explicitly declined pull requests. This rule supersedes the branch/draft-PR publication wording retained in the original phase instructions below; preserve concurrent work and never force-push.

**Reviewed:** 2 October 2026. **Runtime repository:** `TianJieHeng/ryoko-agent`, `main` baseline [`b78931e3b0959c42dca7400c78a4dffd1bb48575`](https://github.com/TianJieHeng/ryoko-agent/commit/b78931e3b0959c42dca7400c78a4dffd1bb48575). The checkout is substantially newer than the supplied Hermes 0.21.1 / `cbd03e6e4ca143c1d5c2db881320afb85783c30b` analysis. Its packaging `0.0.0` placeholder is not a release version; use the recorded commit and the runtime version resolver.

**Build order:** customize Hermes here first; integrate the separately forked [`ryoko-dots`](https://github.com/TianJieHeng/ryoko-dots) afterward. Its reviewed baseline is [`b01ac1f6a903e5e56c119d960901353ac0a3d171`](https://github.com/TianJieHeng/ryoko-dots/commit/b01ac1f6a903e5e56c119d960901353ac0a3d171). The integration boundary is maintained in [Integration_Handoff.md](https://github.com/TianJieHeng/ryoko-dots/blob/main/Integration_Handoff.md).

The user has agreed to configure explicit agent identities. Actual immutable IDs, names, credentials and deployment bindings remain implementation configuration; none are invented or installed by these plans.

## Source precedence and coverage

1. Current user decisions control: two repositories; Hermes first; **only primary Ryoko uses the external personal-memory MCP harness**; every other agent has isolated individual built-in memory; explicitly shared project artifacts are a separate permissioned channel
2. Supplied `upgrade(2).md` and `user-facing-upgrades(2).md` provide the engineering and product requirements, with the LAYA addition dated 23 September 2026
3. Supplied `upgrade.pdf` (50 pages) and `user-facing-upgrades.pdf` (46 pages) were reviewed through full text extraction, normalized comparison and representative visual inspection. No substantive requirements differ from the corresponding Markdown. Covers, contents, wrapping, repeated headers and printed diagram source are presentation differences
4. Current source wins over old baseline assertions and stale area documentation. Observations below are bounded static source checks, not a runtime/security certification

Coverage vocabulary: **U01–U41** engineering upgrades; **F01–F35** product capabilities; **DP01–DP16** LAYA points; **T01–T18** engineering starter tickets; **P01–P16** product tickets. The original “35 upgrades” introduction predates U36–U41. BE and FE phase IDs are this plan pair's implementation sequence, not replacements for source IDs. No scope item disappears because it is conditional or deferred.

The private source PDFs/Markdown are not copied wholesale into this public repository. Their section/ID references below identify the requirements; pinned repository links identify observed implementation. Published/vendor performance figures in the inputs are not measurements of this combined system.

## Non-negotiable ownership

| Concern | Authoritative owner | Boundary |
|---|---|---|
| Session/mission state, orchestration, budgets, effects, approvals, schedules | Hermes runtime | Clients submit commands and display committed projections |
| Primary Ryoko personal durable knowledge | User-selected external MCP memory harness | Only trusted primary identity can attach, discover, invoke or obtain credentials |
| Other agents' individual memory | Existing built-in backend extended with isolated per-agent ownership | Persistent specialist and ephemeral child lifecycles are distinct; no shared “other agents” bucket |
| Shared project work | Authorized project/artifact store | Share selected accepted artifacts/context, not a personal-memory database or implicit credentials |
| Actual output bytes and versions | One designated artifact store per item | Runtime references identity/version; UI does not create a competing editable authority |
| Live calendar/inbox/repository/provider state | Respective external service | Memory may locate evidence, but current facts are rechecked |
| LAYA decisions | Typed LAN decision service supplies calibrated signals | Hermes deterministic code owns policy, budgets, approval floors and effects |
| OpenDots UI and channels | Future consumer in `ryoko-dots` | No second generative/tool loop or schedule executor for migrated missions |

Memory topology is enforced through identity, server/tool grants, credential broker, memory lifecycle, filesystem/process isolation and egress, including tool-search, raw HTTP, shell/code, reconnect, cron and background paths. Prompt instructions or hidden buttons are insufficient. Preferences never grant permission. A project grant does not make a specialist Ryoko.

## Latest LAYA rollout decision

All sixteen decision points remain in the catalog so the runtime can support them, but they are **not sixteen models, sixteen mandatory datasets, or a prerequisite for finishing Hermes**. Router and Guard are proposed reusable model families. The initial LAYA experiment uses the existing intention-routing dataset for front-door/tool-intent work (DP16), after verifying its actual schema, labels, license and held-out quality. A dataset alone is not a trained, calibrated model.

After the agent and real memory integration are built, create an **additional harness-decision dataset** for Ryoko's recall decisions (DP05): actual supported operations, project/personal/none scopes, stale or ambiguous context, denial, defer/unclear, outage and invalidation cases. Use privacy-safe fixtures and an independent holdout. This is a later scoped deliverable, not permission to export private harness records or train/deploy anything now. All other points retain existing deterministic or auxiliary-LLM behavior until a separately evaluated rollout is useful and approved. Per-point evaluation does not imply one checkpoint per point.

## Current fork review and the seams to preserve

This is an evidence map of the inspected execution paths, configuration and representative tests. It is not a claim that every one of the repository's files was read or that inherited tests passed in this environment. Source inspection did not import/run agent code.

| Verified at the pinned fork | Planning consequence | Evidence |
|---|---|---|
| AIAgent is a facade with topical loop modules and an existing TurnContext | Extend those seams; do not recreate a god-file or duplicate context type | [agent/turn_context.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/agent/turn_context.py) |
| Durable session-turn holder leases and transcript write fencing exist | U02 extends ownership to effect/child/delivery authority; integer generation is proposed, not current | [agent/turn_facade_lease.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/agent/turn_facade_lease.py) |
| Lease acquire/refresh/release and compression publication live in root SessionCompressionMixin | Use actual root module, not the old guessed agent/session_compression.py path | [hermes_state_compression.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/hermes_state_compression.py) |
| archive_and_compact is transactional and preserves concurrent tails/originals | U19 protects and extends current commit fences and fidelity | [hermes_state_messages.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/hermes_state_messages.py) |
| Iteration counters are independent per agent; delegated child receives iteration_budget=None | U06 must add aggregate durable reservations; max iterations is not a tree spend cap | [agent/iteration_budget.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/agent/iteration_budget.py) |
| Delegated children use skip_memory=True and exclude memory tools; built-in paths are profile-based | Requested individual child memory requires identity/storage/lifecycle work, not a toggle | [tools/delegate_tool.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/delegate_tool.py) |
| Profile secret/MCP scopes exist, but same-profile agent grants are not the same boundary | Add primary-only server/tool/credential/filesystem/egress enforcement | [agent/secret_scope.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/agent/secret_scope.py) |
| Current cron construction uses skip_memory=False; background review disabled there | Source overrides stale cron guidance; bind cron to the intended agent backend | [cron/scheduler.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/cron/scheduler.py) |
| Child concurrency default in code is 10, with separate one-shot defaults | Do not copy the stale area-document value 3 as configuration truth | [tools/delegate_tool_config.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/delegate_tool_config.py) |
| ProviderProfile plus ProviderTransport preserve provider-only data | agent/provider_base.py is a tool-backend base, not the complete inference interface | [providers/base.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/providers/base.py) |
| MCP snapshots use generation fences and stable prefixes; mutation transport loss can return outcome_uncertain | Preserve these guarantees and layer per-agent grants and durable effect semantics | [tools/mcp_tool_agent.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/mcp_tool_agent.py) |
| Prompt/resource list-change notifications are still logged and ignored | U12/T10 remains a specific current refresh gap to implement or expose explicitly | [tools/mcp_tool_health.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/mcp_tool_health.py) |
| CUA sanitizer failure fallbacks can return unsanitized environments | U10/T03 is a grounded hardening lead; exploitability was not established | [tools/computer_use/cua_backend.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/computer_use/cua_backend.py) |
| The permission-probe helper also falls back to dict(os.environ) | Cover both main driver launch and probe path in sanitizer-failure tests | [tools/computer_use/permissions.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/computer_use/permissions.py) |
| Gateway final delivery is best-effort bounded at-least-once with visible recovery markers | Do not label current transport behavior exactly-once; decide durable-intent failure policy | [gateway/delivery_ledger.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/gateway/delivery_ledger.py) |
| Cron claimed delivery becomes unknown after dead owner and is not blindly replayed | Unify user vocabulary while retaining lane-specific retry safety | [cron/delivery_queue.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/cron/delivery_queue.py) |
| Async delegation persists dispatch/completion/delivery claims, but live children remain process-local | Durable result recovery is existing; arbitrary child execution resumption is proposed | [tools/async_delegation.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tools/async_delegation.py) |
| Projects are existing per-profile multi-folder records with IDs and RPCs | Extend projects; do not build an unrelated second project catalog | [hermes_cli/projects_db.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/hermes_cli/projects_db.py) |
| JSON-RPC models generate TypeScript/OpenRPC; replay uses an in-process bounded epoch/ring | Narrow U32 builds on existing contracts; durable restart replay still needs BE02 semantics | [tui_gateway/event_replay.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tui_gateway/event_replay.py) |
| Goals/control snapshots and server-to-client pending requests already exist | Extend shared managers and request replay rather than new surface-specific parsers | [tui_gateway/methods_session_control.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tui_gateway/methods_session_control.py) |
| PM and transactional update/recovery infrastructure exist | Follow current dependency/build and upgrade contracts; no raw in-place pip/uv mutation | [CONTRIBUTING.md](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/CONTRIBUTING.md) |

Also preserve provider `api_content`/opaque sidecars, cache ordering and fingerprints, image-token estimators as estimates, exact identifiers, separate memory/skills/session search, catalog versus exposure, intent-before-effect ordering, hardline/default-deny floors, endpoint-bound credentials and privacy-separated telemetry. Current MCP read-only annotations do not establish trust. Existing tests below are starting points, not test-pass claims.

## Working method and the build chronicle

Phases are dependency groups, not equal-sized sprints or calendar estimates. Split them into small vertical commits. Deliver a useful Slice-A experience before attempting every advanced feature. Testing, telemetry redaction, data retention and authority checks start with the data/effect they protect; their later consolidation phase is not permission to postpone them.

Every phase begins in `planned`. Its future journal entry should use `planned | in_progress | blocked | verifying | complete | deferred`, with a reason and next prerequisite. The shared root [buildjournal.md](buildjournal.md) is created with the planning entry in this documentation task. Future implementation entries append there; no separate per-phase journal tree is required.

For each implementation slice:

1. Re-read root and affected-area `AGENTS.md`, relevant local skills, current code and motivating tests; verify the base SHA and upstream drift
2. Create `build/beNN-<topic>` or `build/feNN-<topic>` branch from the approved base; keep architectural extraction separate from behavior changes when feasible
3. Add a focused failing behavioral test for the invariant, implement the narrow change, and run the relevant group using the repository-supported runner. Avoid brittle source-shape or frozen-value tests
4. Update root `buildjournal.md` under the matching BE/FE phase heading with objective, source IDs, base/head SHA, files changed, schema/contract versions, migration/rollback, commands and exact results, unresolved questions, receipts/artifacts and next dependency
5. Keep unrelated files unchanged. Commit readable checkpoints; never overwrite user edits or reset a dirty tree. Open a draft PR unless otherwise requested, inspect the full diff, and verify remote SHA/CI before claiming publication
6. Mark complete only when the phase's exit evidence exists. Failed, flaky, skipped, mocked and live-unverified results stay distinct. A docs-only commit does not establish an implemented or passing capability

No phase permits bypassing confirmation, credential or consequential-action policy. New credentials, persistent access, purchases, production deployment, high-impact sends and destructive changes require the appropriate approval at implementation time.

### Release slices and dependency spine

| Slice | User outcome | Minimum coordinated work |
|---|---|---|
| A | Project → scoped context → complete Markdown artifact → precise revision/template → later resume | BE00–BE08 as applicable; narrow BE09/BE10; FE00–FE03; mandatory identity/memory/effect/data gates |
| B | Bounded two-output mission and cited connected research | BE09/BE10; FE04/FE05 |
| C | Manual reusable workflow → useful monitor → bounded condition | BE11/BE12; FE06 |
| D | One or two data/teaching/coding/creative production packages | BE10/BE11/BE13 where required; FE07/FE09 |
| E | Meeting outcomes, correspondence, accepted commitments and briefing | BE12; FE08/FE11 |
| F | Specialists, voice and same-work handoff across surfaces | BE13; FE10; later FE14 in Dots |
| G | Measured tutoring, demonstration, browser/device/team/opportunity experiments | Owning BE/FE phases; feature-specific value gate before enabling |
| LAYA | Per-point shadow, calibration, then qualified enforcement | BE15–BE17 and FE12, interleaved only after explicit prerequisites |
| Final | Consolidated verification and separate Dots readiness/cutover | BE18/FE13/FE14 plus Dots OD00–OD04 |

Mandatory safety gates apply even to a narrow slice. Optional U23 indexing, U24 federation, multi-host storage, additional deployment profiles and broad domain breadth remain explicit conditional work, not hidden prerequisites or automatically authorized expansions.

## Backend phase index

| Phase | Work package |
|---|---|
| [BE00](#be00) | Pin the fork and establish the evidence baseline |
| [BE01](#be01) | Make configuration identity and runtime services explicit |
| [BE02](#be02) | Unify ownership durable transitions and the narrow runtime API |
| [BE03](#be03) | Bound budgets admission concurrency and cancellation |
| [BE04](#be04) | Formalize provider retries and authorized tool views |
| [BE05](#be05) | Enforce capabilities isolation egress and MCP trust |
| [BE06](#be06) | Journal effects and deliver results with honest receipts |
| [BE07](#be07) | Build project artifact and evidence records on existing stores |
| [BE08](#be08) | Route per-agent memory and preserve context through compaction |
| [BE09](#be09) | Build bounded missions plans and evidence-backed completion |
| [BE10](#be10) | Add evidence retrieval and domain production adapters |
| [BE11](#be11) | Version workflows templates and evaluated procedural learning |
| [BE12](#be12) | Make schedules monitors and commitments durable |
| [BE13](#be13) | Give specialists durable handoffs and scoped executors |
| [BE14](#be14) | Harden operations privacy provisioning and deployment |
| [BE15](#be15) | Introduce typed LAYA decisions and secure the Jetson node |
| [BE16](#be16) | Promote LAYA points individually and prove tool planning |
| [BE17](#be17) | Govern LAYA datasets fine tuning and model releases |
| [BE18](#be18) | Run consolidated release gates and hand off to Dots |

## Backend implementation phases

<a id="be00"></a>

### BE00 Pin the fork and establish the evidence baseline

**Status:** planned. **Objective and value:** Create a reproducible starting point for U33/U31/U35 and distinguish inherited capability from the work still required. This phase enables every later claim of improvement.

**Prerequisites:** None. Read root and affected-area AGENTS.md; preserve the supplied design files as input evidence outside implementation changes.

**Files and ownership:** Existing: AGENTS.md, CONTRIBUTING.md, pyproject.toml, uv.lock, package.json, scripts/run_tests.sh, scripts/ci/classify_changes.py, tests/, evals/. Proposed: docs/build/baseline.md, docs/build/decisions/, buildjournal.md (requested shared root journal). All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Record the actual checkout, source commit, Python/Node/package-manager requirements, supported OS/backend combination, enabled providers, profile configuration and extension manifests. The supplied 0.21.1 baseline is historical; do not downgrade this fork to make old file maps fit.
2. Inventory existing session leases, typed JSON-RPC contracts, event replay, projects, goals, compaction, memory providers, tool-search bridge, scheduler ledgers and tests. Trace a CLI turn, TUI turn, gateway turn, cron turn and delegated child with the same identity fixture.
3. Build a baseline fixture set from existing tests rather than copying a parallel harness: two conflicting profiles/agents/projects, a held approval, provider timeout, crash around effect acceptance, lost delivery, compaction restart and unavailable memory.
4. Choose a small certified first deployment and creator/builder pilot. Record exclusions and threat model: trusted host owner versus untrusted generated code, local versus remote executor, private versus shared channel. Do not claim hostile-tenant isolation from profiles.
5. Create an ADR register and journal template. Turn each phase into reviewable vertical tickets; record existing failures separately from regressions. Capture deterministic behavior first; live model measurements need a separately declared repeated paired-trial protocol.

**Interface and data contract:** BaselineManifest {source_sha, dependencies_digest, config_redacted_digest, runtime_versions, os_backend, fixture_version, feature_flags, observed_results}; every result says passed, failed, blocked or not run.

**Failure and security boundary:** Never run the application against production HERMES_HOME while establishing a baseline. Scrub credentials and private traces from fixtures. A source read is not a runtime proof.

**Focused checks:** Planning gate: verify paths, requirements coverage, links and clean docs-only diff. Implementation gate: run the smallest existing fixture groups via scripts/run_tests.sh and record results; do not repeatedly execute the full suite for planning.

**Acceptance and exit:** An implementer can reproduce the chosen fixture baseline and identify precisely which claims are static observations, measured results or proposed targets.

**Integration and rollback:** No runtime migration. Revert baseline documentation/fixture changes independently; retain any measured failure evidence and original inputs.

**Chronicle and Git checkpoint:** append a `BE00` entry to root [buildjournal.md](buildjournal.md); branch `build/be00-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be01"></a>

### BE01 Make configuration identity and runtime services explicit

**Status:** planned. **Objective and value:** Establish U01/U10/U31 and the first U28 hooks. This is the prerequisite for Ryoko-only personal memory, exact approvals and truthful per-surface setup.

**Prerequisites:** BE00. The stable primary agent identity and specialist identity lifecycle are design decisions to record before persistence begins.

**Files and ownership:** Existing: agent/agent_init.py, agent/secret_scope.py, hermes_constants.py, hermes_cli/config_defaults.py, hermes_cli/config_effective.py, hermes_cli/config.py, gateway/run.py, tui_gateway/model_switch.py. Proposed: agent/runtime_context.py, agent/effective_config.py, agent/agent_identity.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Extend existing agent/turn_context.py::TurnContext rather than introducing a competing turn payload. Define the broader immutable AgentContext/runtime service binding with profile/principal/agent/session/project/run identity, policy/config version references, budget reference and narrow ProviderAdapter, ToolExecutor, SessionStore, EventSink and MemoryRouter handles. Never expose the whole AIAgent or a raw secret dictionary.
2. Wrap current config loaders with one typed effective snapshot and provenance model; preserve presence-sensitive semantics and managed overlays. Register every new YAML field and real reader together. Non-secret knobs belong in config.yaml; existing atomic round-trip writers preserve comments.
3. Resolve primary Ryoko versus stable specialist versus ephemeral child identity in trusted construction code. Do not accept a model-supplied name, client label or prompt claim as authority to become Ryoko. Define owner and lifecycle for each individual memory namespace.
4. Add per-agent policy with memory_backend, allowed MCP servers/tools, secret references, egress purposes and project grants. Primary-only personal MCP access is deny-by-default for every other agent even when sharing a profile.
5. Bind profile home, secrets and terminal scope together on turns, RPC, cron, teardown, background reviews, thread hops and child construction. An installed scope with a missing credential stays denied; never repair it with ambient environment fallback.
6. Expose redacted effective configuration for a specified agent/session and surface. Keep old config readable; derive deterministic migrations and document restart versus next-session effects. Migrate one composition root at a time, then prove parity across the others.

7. Document hook ordering, allowed argument transformation and failure semantics at each boundary. Test that plugin/hook exceptions cannot skip required authorization, mutate immutable context or dispatch twice; preserve existing once-only middleware behavior.

**Interface and data contract:** IdentityBinding {principal_id, profile_id, agent_id, session_id, project_id?, mission_id?, surface_thread_id?, runtime_owner, binding_revision, provenance}; AgentPolicy {policy_version, role, memory_backend, mcp_grants, secret_refs, project_grants, egress_policy_ref}; EffectiveConfig is immutable for a run, carries field provenance but no secret values.

**Failure and security boundary:** Forged agent IDs, unknown backends, unresolved credential scope, cross-profile fallback and invalid config fail before execution. Backend selection is security policy, not a renderer or prompt preference. Never print token values in inspect output.

**Focused checks:** Focused: A→B→A live profile tests using two on-disk homes; same-profile Ryoko versus specialist identity; entry-point effective-policy equivalence; secret redaction; invalid-field/migration and hook-order/failure/double-dispatch fixtures. Extend existing config/profile/secret-scope tests.

**Acceptance and exit:** All construction routes resolve the same intended policy; no non-Ryoko identity can obtain the personal harness grant or credential. Every new config field has a validated reader and generated reference.

**Integration and rollback:** Ship behind an explicit policy/config schema version. Preserve old field reads through a bounded migration period; rollback must refuse newer policy records it cannot enforce, rather than silently widen access.

**Chronicle and Git checkpoint:** append a `BE01` entry to root [buildjournal.md](buildjournal.md); branch `build/be01-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be02"></a>

### BE02 Unify ownership durable transitions and the narrow runtime API

**Status:** planned. **Objective and value:** Extend U02/U03/U30 and bring the minimum U32 forward. A single authoritative execution history is needed before another client can safely reconnect or steer.

**Prerequisites:** BE01; reuse existing lease, state and contract systems. Design the version negotiation and command idempotency rules with FE00 and the Dots handoff before code changes.

**Files and ownership:** Existing: agent/turn_facade_lease.py, hermes_state_compression.py, hermes_state.py, hermes_state_holders.py, hermes_state_messages.py, hermes_state_schema.py, gateway/lifecycle_ledger.py, gateway/session_persistence.py, tui_gateway/contracts/, tui_gateway/event_replay.py, apps/shared/src/json-rpc-gateway.ts. Proposed: agent/session_coordinator.py, hermes_state_events.py, hermes_state_commands.py, hermes_state_checkpoints.py, tui_gateway/contracts/runtime_v1.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Inventory the inherited cross-process turn lease and holder-fenced writes. Extend the existing holder fencing to every authoritative transition and broker dispatch. Introduce a monotonic owner generation only through an explicit lease/schema migration if the new contract requires it; do not introduce a competing lease table without a sole-writer decision.
2. Define transactional SessionStore operations for claim/renew/release, append-if-generation, command deduplication, checkpoint publication, effect/outbox association, snapshot and cursor reads. Keep SQLite first, with bounded transactions and external blob references.
3. Persist accepted commands and typed nondeterministic outcomes with stable event IDs, session sequence, schema version, owner generation and correlation IDs. UI/transcript/search projections derive from committed facts; transitional dual writes require one transactional outbox plus reconciliation and an explicit retirement date.
4. Extend generated Python/Pydantic-to-TypeScript contracts. Preserve existing JSON-RPC transport and compatibility negotiation where suitable; add transport-neutral semantics rather than inventing gRPC or a second unrelated RPC stack.
5. Persist durable replay cursors for mission transitions. The current in-process epoch/ring remains useful for token streaming but is not restart-durable mission history. On expired cursor return snapshot_required and a consistent snapshot+cursor, never an apparently empty success.
6. Checkpoints store included sequence, unresolved effects, config/policy/runtime versions, prompt projection version and artifact references. Recovery replays recorded outputs, not tools or model calls. Migrate old sessions with explicit compatibility status.
7. Implement submit/read/replay/steer/cancel/approval command envelopes as a narrow vertical contract; effectful commands remain gated until BE05/BE06. Clients cannot create a second run by replaying full conversation history.

**Interface and data contract:** RuntimeCapabilities; RuntimeCommand {schema_version, command_id, idempotency_key, expected_revision, identity_binding, operation, payload}; CommandReceipt {command_id, status: accepted|rejected|duplicate, durable_revision, run_id?, conflict?}; RuntimeEventEnvelope {event_id, session_id, seq, cursor, generation, schema_version, mission_id?, run_id?, operation_id?, effect_id?, delivery_id?, approval_id?, occurred_at, type, payload}; MissionSnapshot {revision, state, outstanding_requests, artifacts, unresolved_effects, last_cursor}. Authenticated actor comes from transport, not the untrusted payload.

**Failure and security boundary:** A stale owner cannot append or launch a new effect. A duplicate command returns its original receipt; intentional repeated actions use new command IDs. Unknown versions fail explicitly. Slow clients never block the journal or grow unbounded buffers. Reconnect is not cancellation.

**Focused checks:** Focused: two-worker lease race; stale-holder write/dispatch rejection; crash at acceptance/checkpoint boundaries; duplicate command; restart with high old cursor; ring truncation; snapshot/replay ordering; old/new-client compatibility; generated-contract freshness.

**Acceptance and exit:** One committed command yields one authoritative run, reconstruction survives process death, and a reconnecting client receives one consistent view. Narrow API fixtures are suitable for a later Dots adapter.

**Integration and rollback:** Use additive schema migrations and versioned projections. Rebuild projections from retained events. Rollback reads only supported event versions; drain writers and snapshot before any incompatible storage transition.

**Chronicle and Git checkpoint:** append a `BE02` entry to root [buildjournal.md](buildjournal.md); branch `build/be02-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be03"></a>

### BE03 Bound budgets admission concurrency and cancellation

**Status:** planned. **Objective and value:** Implement U04/U05/U06 across foreground, scheduled, child and auxiliary work. Existing per-agent iteration caps do not constitute a shared tree budget.

**Prerequisites:** BE01/BE02; budget policy and strict-mode provider limits must be explicit.

**Files and ownership:** Existing: agent/iteration_budget.py, agent/conversation_loop.py, agent/turn_*.py, tools/delegate_tool.py, tools/delegate_tool_config.py, tools/process_registry.py, tools/terminal_tool_lifecycle.py, gateway/run_turn_runner.py, cron/scheduler.py. Proposed: agent/budget_account.py, agent/admission.py, agent/task_scope.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Introduce durable parent/child BudgetAccount reservations for spend, tokens, wall time, attempts and concurrency. Reserve before dispatch and settle afterward. Include main/auxiliary inference, retries, compaction, embeddings, memory service charges, MCP sampling, media jobs, LAYA latency and fallbacks.
2. Preserve the existing useful per-agent iteration cap as a local limit while adding an aggregate tree ceiling. A child cannot manufacture a new root account or reset spending by retrying/restarting.
3. Add bounded workload queues with per-principal fairness, interactive priority without starvation, queue expiry, cancellation and disk quotas. Reserve provider and executor slots before work starts.
4. Refactor blocking operations incrementally into managed task scopes, bounded thread pools and supervised process boundaries. Do not require a full async rewrite before fixing authorization or persistence.
5. Propagate deadline/cancellation through provider calls, queued jobs, human waits, browser/terminal processes, children and callbacks. Record local stop, upstream acknowledgment, detached remote work and outcome uncertainty separately.
6. Gracefully drain during shutdown and lease transfer. Accepted queued work must either recover or receive a durable rejected/expired terminal state. Strict-budget mode refuses opaque external loops that cannot satisfy its limit contract.

**Interface and data contract:** BudgetReservation {account_id, parent_id, operation_id, maxima, reserved, actual, unknown_usage, deadline, settlement_state}; Cancellation {request_id, requested_at, local_state, upstream_ack?, pending_effect_ids}; admission outcomes accepted|queued|rejected|expired.

**Failure and security boundary:** No unlimited retry, queue, offload pool or inherited child budget. Unknown invoice usage is visible and conservatively reserved. Local task cancellation cannot promise that a provider stopped billing or an external mutation was undone.

**Focused checks:** Focused: concurrent reservations cannot oversubscribe; kill/restart preserves reservations; queue expiry never launches; noisy principal fairness; cancel during inference/tool/human wait/child; no untracked owned process after stop.

**Acceptance and exit:** All chargeable operations have a root budget and explicit outcome; queue/resource limits hold under declared load; cancellation produces an honest receipt and usable partial result.

**Integration and rollback:** Feature-gate new admission policy, but never bypass established ceilings. Drain queues before rollback, settle or preserve outstanding reservations, and retain uncertain remote operations for reconciliation.

**Chronicle and Git checkpoint:** append a `BE03` entry to root [buildjournal.md](buildjournal.md); branch `build/be03-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be04"></a>

### BE04 Formalize provider retries and authorized tool views

**Status:** planned. **Objective and value:** Complete U07/U08/U27 while preserving protocol fidelity and cache behavior.

**Prerequisites:** BE01–BE03; server/tool grants are checked again at BE05 dispatch.

**Files and ownership:** Existing: providers/base.py, agent/transports/base.py, agent/transports/types.py, agent/client_lifecycle.py, agent/turn_retry_state.py, agent/turn_recovery.py, agent/provider_projection.py, agent/codex_runtime.py, agent/prompt_caching.py, agent/conversation_loop.py, tools/registry.py, model_tools.py, toolsets.py, tools/tool_search.py, hermes_cli/fallback_config.py. Proposed: agent/provider_capabilities.py, agent/attempt_policy.py, agent/tool_view.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Declare streaming, parallel tools, media, usage, cancellation acknowledgment, cache semantics, opaque protocol state and inner-loop ownership for each supported provider. Keep signed reasoning blocks/continuation IDs lossless in a private versioned sidecar.
2. Classify auth failure, quota exhaustion, throttling, overload, context overflow, unsupported capability and ambiguous transport failure separately. Centralize attempt ceilings, jitter, Retry-After and account-key-pool circuit state.
3. Audit SDK retry behavior and nested recovery so the real number of requests stays within BE03. A repair for context overflow must not enter generic throttling retry; a timeout after possible acceptance must retain uncertainty.
4. Allow model/endpoint failover only when recipient, privacy purpose, tool state, capabilities and remaining cost are valid. Bind client pools to secret scope, endpoint, proxy/TLS settings and event-loop ownership.
5. Keep shared immutable catalog metadata and derive a session-scoped installed/discoverable/selected/authorized view. A process-wide check_fn must not decide a session-only capability. Search and reopen filter against the same grants as invocation.
6. Expose clear availability reasons without leaking another identity’s configuration. Preserve the current stable tool-schema prefix; LAYA filtering is shadow-only until BE16 defines a cache-safe boundary.

**Interface and data contract:** ProviderCapabilities with opaque_state_version and execution_owner; Attempt {attempt_id, reason, provider_account_ref, reservation_id, remote_acceptance}; ToolView {catalog_version, session_policy_version, selected_tool_ids, unavailable_reasons}; exposure never equals authorization.

**Failure and security boundary:** No silent fallback to an unauthorized cloud provider, another credential pool or an unsupported protocol. Hiding a tool is not denial: direct invocation and search must both enforce grants. Do not normalize away provider-required fields.

**Focused checks:** Focused: protocol round trips for tool IDs/media/opaque state; retry amplification/Retry-After/circuit fixtures; private-provider failover rejection; same-profile agents with different tool grants; hidden-tool direct invoke denial.

**Acceptance and exit:** Supported provider fixtures pass without information loss; every attempt is attributed; authorized tools remain discoverable while personal MCP stays unreachable to non-Ryoko agents.

**Integration and rollback:** Roll adapters out per provider/profile. Preserve stored opaque-state readers for existing sessions; explicit unsupported status is safer than attempting lossy downgrade.

**Chronicle and Git checkpoint:** append a `BE04` entry to root [buildjournal.md](buildjournal.md); branch `build/be04-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be05"></a>

### BE05 Enforce capabilities isolation egress and MCP trust

**Status:** planned. **Objective and value:** Close U09/U10/U11/U12/U14 boundaries before unattended effects. This is also the enforcement foundation for per-agent memory.

**Prerequisites:** BE01–BE04. Select one actually enforceable OS/executor boundary first; a sandbox directory or prompt is insufficient.

**Files and ownership:** Existing: tools/approval.py, tools/approval_context.py, tools/code_execution_rpc.py, tools/terminal_tool_guards.py, tools/terminal_tool_backends.py, tools/environments/, tools/computer_use/cua_backend.py, tools/computer_use/permissions.py, tools/mcp_tool_health.py, tools/mcp_tool.py and siblings, agent/secret_scope.py. Proposed: tools/capability_broker.py, tools/egress_policy.py, tools/workspace_manifest.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Bind short-lived capabilities to principal, agent, run, owner generation, effect class, resource roots, destination, input digest, policy version and expiry. Evaluate deterministic policy before invoking any executor or model-based gate.
2. Fix verified CUA sanitizer failure paths so privileged launch denies rather than copying unsanitized environment. Preserve existing RPC token checks; strengthen tool scope on both socket and polling transports.
3. Run untrusted generated code/children/plugins without ambient credentials behind an enforceable account/process/VM/container boundary appropriate to the OS. Validate direct shell/network/file attacks as well as registered tool calls. Refuse unsupported modes rather than claiming false isolation.
4. Create isolated child workspaces or Git worktrees with immutable inputs and staged output manifests. Before promotion verify content hashes, paths, symlinks, base revisions, conflicts and permissions. Never silently overwrite parent work.
5. Route all outbound purposes through a recipient plan: main/aux model, memory, embeddings, MCP, browser/tool, message delivery, telemetry, provisioning and training. Include redirects and subprocess egress. LAN Jetson is a recipient; local-only has a declared envelope, not just a local main model.
6. Bind MCP connections and pools to agent identity/grants as well as profile. Enforce server admission, tool dispatch, credentials, sampling, prompt/resource reads, session ingestion and teardown. Implement versioned prompt/resource refresh or explicit unsupported status; schema changes require reauthorization and cache invalidation.
7. Preserve current generation-fenced MCP tool snapshots, prefix restoration and read-only-aware reconnect behavior. Existing outcome_uncertain for potentially mutating calls must survive refactoring. Server readOnlyHint is untrusted metadata, not proof of authorization or idempotency. Modern SDK-owned sampling/MRTR must carry the same caller budget and grant as legacy callbacks.
8. Budget sampling by model/prompt/output/depth/concurrency and initiating run. Revocation prevents future calls and cached discovery; deny indirect access through tool_search, code execution, shell, browser, plugin routes or inherited credentials to Ryoko’s personal harness.
9. Preview consequential actions with exact recipient/target, change digest and policy. A missing/expired/mismatched approval remains pending or denied. An unavailable UI approval surface never means auto-approval.

**Interface and data contract:** Capability {capability_id, actor, agent_id, run_id, generation, operation_class, resource_scope, destination_purpose, approval_digest?, expires_at}; MCPGrant {server_identity, transport, tool_allowlist, read_scopes, sampling_limits, secret_ref, policy_version}; WorkspaceManifest {base_revision, inputs, outputs, hashes, staged_effects}.

**Failure and security boundary:** Fail closed on sanitizer/import failure, unverifiable identity, unavailable enforcement or stale grants. Prevent confused-deputy grants via shared artifacts and prompt injection. No classifier or MCP description can authorize a capability.

**Focused checks:** Focused: cross-agent/profile credential matrix; sanitizer fault; both RPC transports; disallowed path/symlink/network access; direct personal-harness call from a child; malicious MCP schema refresh/sampling recursion; revoke then reconnect; staged merge conflict. Use harmless seeded fixtures.

**Acceptance and exit:** Every certified executor proves its declared boundaries; non-Ryoko identities cannot reach personal MCP by any supported execution route; effect dispatch validates exact live scope.

**Integration and rollback:** Revoke capabilities and stop unsafe executors before rollout reversal. Keep the hardened denial defaults on rollback. Retain staged work for inspection without promoting or executing it.

**Chronicle and Git checkpoint:** append a `BE05` entry to root [buildjournal.md](buildjournal.md); branch `build/be05-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be06"></a>

### BE06 Journal effects and deliver results with honest receipts

**Status:** planned. **Objective and value:** Implement U15/U16 by integrating existing ledgers, giving missions and clients reliable external-action semantics.

**Prerequisites:** BE02/BE03/BE05; one selected mutation adapter and one delivery adapter first.

**Files and ownership:** Existing: gateway/lifecycle_ledger.py, gateway/delivery_ledger.py, gateway/run_delivery_*.py, gateway/session_persistence.py, tools/send_message_tool.py, cron/executions.py. Proposed: hermes_state_effects.py, agent/effect_reconciler.py, gateway/durable_outbox.py, adapter-specific reconciliation modules. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Persist effect intent and stable operation ID before dispatch. Canonicalize target and arguments by operation type; distinguish a retry from a new intentional identical action. Record provider idempotency support honestly.
2. Use states prepared, dispatched, confirmed, failed, outcome_unknown and reconciliation_required. Persist receipt/evidence and generation; a transport timeout after possible remote acceptance never becomes automatically retryable.
3. Implement one read-only reconciler that queries the provider receipt/current state, bounded by scope and deadlines. Where reconciliation is impossible, hold and show exactly what is unknown; do not ask the model to guess success.
4. Commit final artifact/version and delivery intent through the authoritative transaction/outbox. Delivery attempts use the same committed payload and recipient identity; they must not rerun inference or effects.
5. Implement bounded delivery attempt count, jitter/backoff, deadline/retention, dead-letter or visibly unresolved queue and authorized manual repair; a retry policy never overrides non-idempotent uncertainty. Track acknowledgment level per platform: accepted by API, message ID known, attachment accepted, user receipt when genuinely available. Text plus attachment may be partially delivered.
6. Bind approval resolution to actor, action digest, target, policy version, relevant artifact/input revision, expiry and owner/run context. Reject a repeated answer after consumption or a changed action. Propagate cancellation as local stop plus unresolved external-effect set.
7. Reconcile inherited delivery and lifecycle records into the new model using explicit mappings; never keep two authorities deciding completion indefinitely.

**Interface and data contract:** EffectReceipt {operation_id, effect_id, input_digest, target_ref, state, idempotency_key?, remote_receipt?, reconciliation_evidence?, generation}; DeliveryReceipt {delivery_id, artifact_id, version, destination, state, acknowledgment_level, platform_ids, attempt_count}; ApprovalRequest/ApprovalDecision bind exact immutable action scope.

**Failure and security boundary:** Unknown is neither failed nor successful. Retry delivery independently. Do not claim exactly-once external mutation where the provider cannot enforce it. A cancellation request cannot undo already confirmed effects.

**Focused checks:** Focused: crash before/after dispatch and remote acceptance; duplicate webhook/submit; lost receipt; parameter-changed approval; stale owner; attachment-only delivery failure; retry delivery does not call agent; process restart with unresolved effect.

**Acceptance and exit:** No unresolved non-idempotent effect is automatically repeated. Every result has independent execution and delivery status, and a user can recover a committed output after delivery failure.

**Integration and rollback:** Drain the outbox or retain compatible readers before downgrade. Never drop unresolved effects or reset idempotency history. Feature rollback disables new effect classes while reconciliation remains available.

**Chronicle and Git checkpoint:** append a `BE06` entry to root [buildjournal.md](buildjournal.md); branch `build/be06-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be07"></a>

### BE07 Build project artifact and evidence records on existing stores

**Status:** planned. **Objective and value:** Provide the shared object backbone for F01/F03/F04/F10/F11/F32 and U20 without treating personal memory as a project database.

**Prerequisites:** BE02/BE05/BE06; narrow artifact staging may begin earlier read-only.

**Files and ownership:** Existing: hermes_cli/projects_db.py, tui_gateway/methods_projects.py, tui_gateway/contracts/projects_pets.py, tools/project_tools.py where applicable, hermes_state_sessions.py, hermes_cli/web_routers/files.py. Proposed: agent/project_context.py, hermes_cli/artifact_store.py, agent/evidence_ledger.py, tui_gateway/contracts/artifacts.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Extend the existing per-profile Project and multi-folder records with purpose, canonical source/artifact references, mission associations and explicit agent/principal project grants. Preserve existing project IDs/slugs/folders and avoid a second unrelated project catalog.
2. Add Capture records retaining original bytes/source URL, acquisition time, extraction status, annotation, suggested project and reversible filing. Deduplication proposes consolidation; it must preserve distinct dates/annotations and never delete originals silently.
3. Create immutable ArtifactVersion records with content digest, format/MIME, size, provenance, producing run, parent version, declared derivatives, validation receipt, approval status and storage locator. Store large blobs outside hot session rows with authorization on every read.
4. Implement compare-and-swap revision/edit contracts, locked sections and derivative invalidation. A targeted revision preserves unrelated approved content; conflicting external edits produce a reviewable branch.
5. Templates hold explicit reusable structure/style/assets/slots and exclusions, separate from incidental content and preferred-template memory pointers. Branches reference immutable baselines and can compare/merge only approved deltas.
6. Create typed EvidenceAnchor records for source spans, IDs, decisions, constraints, approvals and artifact versions. Record freshness and authority class; relevance/confidence is not proof. A resume package joins live mission state, current versions, blockers and bounded authorized context.
7. Publish complete files and verifiable references through existing controls. Local preview/download success is checked separately from external sharing; permissions stay least-privilege.

**Interface and data contract:** Project {existing_id, revision, purpose, folders, source_refs, canonical_artifact_refs, active_mission_refs, grants}; ArtifactRef {artifact_id, version, digest, mime, size, project_id, lineage, validation, access_ref}; EvidenceAnchor {source_ref, version, range, captured_at, authority, validity}; Capture and Template each have immutable source/version lineage.

**Failure and security boundary:** Project artifacts are deliberately shared resources, not shared personal memory. Guard path traversal, symlink escape, MIME confusion, active-content preview, stale revisions, incomplete exports and revoked access. Extraction failure leaves the original usable.

**Focused checks:** Focused: same-name projects in two scopes; old project-schema migration; targeted edit unchanged sections; stale base conflict; derivative invalidation; complete download digest; capture extraction failure; cross-agent artifact read only with grant; malicious preview isolation.

**Acceptance and exit:** P01/P04/P05/P06 can be demonstrated with one complete Markdown artifact and correct version recovery. Sources and generated outputs remain distinguishable and traceable.

**Integration and rollback:** Use additive records and preserve original files. Revert a canonical pointer to a previous version instead of overwriting history. Garbage collection waits for retention/reference checks and backup policy.

**Chronicle and Git checkpoint:** append a `BE07` entry to root [buildjournal.md](buildjournal.md); branch `build/be07-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be08"></a>

### BE08 Route per-agent memory and preserve context through compaction

**Status:** planned. **Objective and value:** Implement the corrected U19–U24 boundary. Ryoko alone uses the external personal MCP harness; other agents receive isolated built-in individual memory.

**Prerequisites:** BE01/BE02/BE05/BE07. Verify the real personal memory MCP contract before relying on unsupported operations.

**Files and ownership:** Existing: agent/memory_provider.py, agent/memory_manager.py, hermes_state_compression.py, agent/context_compressor.py, agent/prompt_builder.py, agent/prompt_caching.py, tools/memory_tool.py, tools/memory_tool_store.py, tools/session_search_tool.py, tools/delegate_tool.py, plugins/memory/. Proposed: agent/memory_router.py, agent/individual_memory_scope.py, external personal-harness adapter/plugin, tests/agent/test_agent_memory_isolation.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Wrap the current built-in memory implementation as the default backend behind explicit per-agent routing. Give every non-Ryoko stable specialist and delegated child an individual namespace with owner, lifecycle and retention. Current children use skip_memory=True and exclude memory tools: replace that behavior deliberately with child-specific memory construction, not shared profile memory.
2. Decide stable specialist identity versus ephemeral task child lifetime. Ephemeral children may use ephemeral isolated built-in memory and archive/delete it under policy; a future named specialist must not inherit an unrelated previous child’s namespace. Never repoint multiple agents to a shared MEMORY.md/USER.md.
3. Configure Ryoko’s personal backend using the actual MCP server identity/tool/schema/credential contract. Verify recall scope, source/version/freshness, bounded context packages, write/supersession acknowledgments, conflicts, deletion/export and invalidation. Unsupported operations stay visibly unsupported.
4. Enforce primary-only access at construction, server connections, tool admission/dispatch, credentials, sampling, egress, memory hooks, search and teardown. Exercise two agents in the same profile. Specialists receive only explicitly shared project artifacts/context, never the personal store or its credential.
5. On harness outage Ryoko may use already authorized active context and project artifacts with a degraded-context notice. Do not create a hidden built-in personal store, silently fall back to another provider, or queue unbounded private writes. Built-in agents remain independent of harness availability.
6. Preserve transactional compression, summary fallback/rehydration, exact evidence anchors and provider sidecars. Commit projection and checkpoint atomically with source-watermark checks; retain concurrent tails and reject torn checkpoints.
7. Scope provider cache identity and reuse by authenticated agent/privacy boundary, credential scope and endpoint; cache equality cannot authorize cross-agent reuse of private context. Keep the stable prompt prefix byte-stable for a conversation. Corrections/supersession enter a bounded fresh context segment with record version/invalidation, or an explicit cache-aware session boundary. Do not reload all memory/tools or rewrite past messages mid-turn.
8. U23: do not build a duplicate personal semantic index for Ryoko. Preserve built-in lexical/session search for others; any optional local hybrid index for an individual agent requires an explicit measured need, scope/deletion parity and later approval. U24: retain a provider capability interface but no default fan-out/federation. The harness may own its internal retrieval without becoming every agent’s backend.

9. For each non-Ryoko built-in store, add structured record semantics while preserving a validated human-readable projection: record ID, owner agent, kind (stated fact, inference, preference, decision or procedure reference), source/author, created/updated time, valid-from/to, version, confidence/validity, scope and deletion state. Use compare-and-swap writes and explicit conflicting/superseded records rather than silently overwriting concurrent corrections. Private procedure references still point to canonical versioned workflows.
10. Conditional U23 subplan, only if a measured individual-agent retrieval need justifies it: permission-filter candidates before lexical exact-ID plus semantic fusion/reranking; retain source ranges/timestamps, embedding-model/index version and bounded context cost. Rebuild on model incompatibility and propagate correction/deletion to every derived index. Compare lexical-only versus hybrid on exact IDs, paraphrases, false positives, latency/tokens and ACL/deletion fixtures before enablement. Never index Ryoko’s personal harness data in a duplicate agent-side semantic store.
11. Conditional U24 subplan: declare per-backend read/write/profile/session-ingest/delete/export capabilities with one authoritative writer per record class. Disabled providers receive zero startup, recall, write or teardown payload. No default fan-out. Any later authorized read federation names origin/scope, deduplicates without hiding disagreement, bounds partial failure, and proves export/deletion/migration parity before activation. It cannot grant a non-Ryoko agent access to the personal harness.

**Interface and data contract:** MemoryRecord {record_id, owner_agent_id, kind, source_ref, author, created_at, updated_at, validity, version, confidence, scope, deletion_state}; MemoryBackend {recall, write?, supersede?, delete?, export?, health, capability_manifest}; MemoryRequest {authenticated_agent_id, backend_binding, project_grants, scope, purpose, context_budget, expected_version?}; MemoryResult {records, provenance, freshness, conflicts, supported_operations, acknowledged_version, degraded}; ContextProjection {source_seq_range, immutable_prefix_digest, fresh_context_versions, anchors, sidecar_version, fallback_state}.

**Failure and security boundary:** No prompt-only isolation, name-based primary spoofing, ambient credentials, model-directed backend switch, cross-child store reuse or personal recall through generic tool search. A memory preference is not authorization. Deletion across remote copies requires actual acknowledgments.

**Focused checks:** Focused: Ryoko/specialist/child A→B→A memory lifecycle; direct and indirect MCP denial; child survives/resumes with its own memory; same-profile isolation; harness outage; concurrent built-in corrections/CAS conflict and validated projection; disabled-provider zero-payload lifecycle; conditional hybrid ACL/delete/rebuild and exact/paraphrase comparison; correction reflected next relevant output but not unrelated project; compaction exact IDs/approval anchors/conflicts; prefix digest stable.

**Acceptance and exit:** P02/P03 and F05/F06 pass for Ryoko; every other agent can use only its own built-in memory plus granted artifacts. No hidden personal-memory duplication; compaction preserves recoverable authoritative state.

**Integration and rollback:** Never downgrade by copying personal harness data into a default shared store. Revoke external grants before backend migration, retain namespace ownership metadata, and restore prior context projections/record pointers only when compatible and authorized.

**Chronicle and Git checkpoint:** append a `BE08` entry to root [buildjournal.md](buildjournal.md); branch `build/be08-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be09"></a>

### BE09 Build bounded missions plans and evidence-backed completion

**Status:** planned. **Objective and value:** Connect F02/F09/F10/F26 with U26 and existing goals rather than running a second autonomous loop.

**Prerequisites:** BE03–BE08; minimal mission object can begin with artifact-only work.

**Files and ownership:** Existing: hermes_cli/goals.py, hermes_cli/goal_command.py, agent/turn_stop_gates.py, agent/tool_guardrails.py, agent/plan_prompt.py, tools/approval_smart.py, tui_gateway/methods_session_control.py. Proposed: agent/mission_contract.py, agent/mission_verifier.py, hermes_state_missions.py. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Extend existing goal contracts into a Mission with requested outcome, deliverables, acceptance criteria, scope, budget, deadline, owner, dependencies, artifact refs and current revision. Preserve shared goal command parsing and manager ownership across surfaces.
2. Use direct execution for bounded low-risk tasks and explicit plan/checkpoints for high uncertainty or consequential steps. Ask only for missing decisive input; allow a plan revision without discarding accepted artifacts.
3. Keep a state machine with ready, working, waiting_for_user, waiting_for_source, ready_to_review, completed, partially_completed, paused, cancelled and failed; execution terminal state and delivery state remain independent fields.
4. Create deterministic validation receipts for required files, section/schema checks, exact requested changes, test commands and cross-artifact consistency. Model judges supplement rather than replace proof. A stopped model or successful API response does not mean the mission is complete.
5. Invalidate only affected pending approvals when a plan/target/input digest changes. A steer arriving too late records missed_steer and identifies irreversible completed effects; never claim retroactive correction.
6. Persist no-progress evidence and bounded recovery choices. If blocked or budget-exhausted, return usable completed artifacts, exact remaining work and next decision without inventing follow-up tasks.
7. Compare reviewed versus direct policies on matched tasks, measuring total user review time as well as agent cost/latency. Implement mission controls once in backend and project them through CLI/TUI and later Dots.

**Interface and data contract:** Mission {mission_id, project_id, agent_id, revision, outcome, deliverables, acceptance, scope_ref, budget_ref, state, next_step, blockers, artifact_refs, effect_refs, delivery_refs}; VerificationReceipt {criterion_id, artifact_version, verifier, evidence_ref, result, observed_at}.

**Failure and security boundary:** No fabricated tests or results, self-approved privilege expansion, infinite judge loop or conversion of source text into user instructions. User acceptance, deterministic validation and delivery acknowledgment are separate facts.

**Focused checks:** Focused: two linked artifacts and consistency gate; partial completion; changed source invalidates only affected work; steer/complete race; approval expiry; judge parse failure; no-progress halt; cancel with already-dispatched effect.

**Acceptance and exit:** P07 succeeds: both outputs are complete, consistent and correctly marked ready/blocked. The runtime explains why each completion criterion passed and what remains unresolved.

**Integration and rollback:** Keep existing goal-compatible commands readable. Disable new autonomous continuation while retaining snapshots, artifacts and receipts. Resume only after revalidating current scope and unresolved effects.

**Chronicle and Git checkpoint:** append a `BE09` entry to root [buildjournal.md](buildjournal.md); branch `build/be09-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be10"></a>

### BE10 Add evidence retrieval and domain production adapters

**Status:** planned. **Objective and value:** Deliver F07/F08/F09/F12/F13/F14/F15/F25/F26/F28 through reusable artifact/mission contracts rather than expanding the core tool catalog indiscriminately.

**Prerequisites:** BE05–BE09; each adapter additionally needs its actual connector/tool and purpose-specific grant.

**Files and ownership:** Existing: tools/, skills/, optional-skills/, plugins/, tools/session_search_tool.py, agent/provider_projection.py, evals/, hermes_cli/web_routers/files.py. Proposed: narrow project skills/plugins and format validators, agent/source_manifest.py, versioned domain fixture packages. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Connected research first supports two authorized source types. Resolve authoritative versions, retain evidence spans and coverage/freshness/missing-source status; deduplicate without flattening approved specs and casual messages into one authority class.
2. Living briefs begin with manual refresh against a bounded source manifest. Update only claims affected by new evidence, preserve prior versions, and distinguish factual change from interpretive recommendation change. Scheduled refresh arrives through BE12.
3. Decision/scenario records separate measured facts, assumptions, user priorities, weights/ranges and uncertainty. Deterministic calculations and sensitivity checks recompute when a decisive input changes; subjective scores are labeled.
4. Format adapters produce complete Markdown first, then validated document/deck/spreadsheet/media bundles as supported. Each has import/export capabilities, rendering/openability checks, derivative lineage and precise revision limits. Never promise fidelity solely from filename extension.
5. Data analysis retains original datasets, cleaning steps, formulas/code, units and provenance; reconcile known totals and avoid silently replacing formulas with values. Teaching packages align objectives, source facts, lesson structure, assessments and answers; tutors use verified exercises and bounded adaptation.
6. For F12, implement bounded CSV/XLSX ingestion and profiling, explicit date/currency/null/unit/duplicate-key assumptions, manual ambiguous-column mapping, common joins/aggregations, a small chart set and complete workbook/notebook export. Persist the transformation recipe and row-level lineage; known-answer fixtures must trace each computed result back to source rows.
7. For F14/F15, start one curated tutoring domain with verified answer criteria, hint progression, spaced revisit choices, learner-visible exercise history, reset and difficulty controls; evaluate on a separate transfer exercise rather than repetition. The educator package separately models prerequisites, objectives, lesson sequence/activity duration and objective→slide→exercise→answer-key coverage, preserving instructor edits.
8. Meeting/media ingestion preserves timestamps/speaker uncertainty and separates proposed commitments from accepted obligations. Recording/transcription consent and retention are explicit. Creative pipelines track rights/reference assets, versions and cross-asset continuity.
9. Coding missions use scoped repository/worktree, baseline SHA, changed-file review, targeted validation then release gate; publication/merge/deploy remain distinct permissions. Browser completion uses current page evidence and exact effect receipts, with auth/payment/high-impact handoff where policy requires.
10. Package these as small CLI+skill/service-gated/plugin adapters following the footprint ladder. Reuse deterministic validators and actual supported tools; add integrations only after a first end-to-end task works.

11. Creative-media validators inspect actual dimensions, duration, audio encoding/sample properties, subtitle timing and naming/reference consistency as applicable. A prompt-only stage emits text and manifest only; human/external stages are explicitly pending, never mislabeled generated assets.

**Interface and data contract:** SourceManifest {source_id, version, scope, retrieved_at, freshness, evidence_ranges, errors}; DomainJob {inputs, transformations, artifact_refs, validator_manifest, consent_refs?}; DecisionRecord {options, criteria, facts, assumptions, weights, sensitivity, accepted_choice?}.

**Failure and security boundary:** Untrusted source content never authorizes action. Protect formulas, recordings, copyrighted/private source material and active previews. Missing connector/unsupported export produces partial status, not fabricated output. No inferred commitment becomes a sent message or scheduled obligation.

**Focused checks:** Focused: two-source citation precision; inaccessible/stale source; controlled brief update; assumption sensitivity; CSV/XLSX ambiguous columns, known-answer joins/aggregations/formulas/charts and source-row trace; tutor reset/difficulty and held-out transfer exercise; lesson objective/assessment alignment and answer correctness; transcript uncertainty; artifact re-open/render; coding targeted revision; browser submit timeout reconciliation.

**Acceptance and exit:** P08 and selected Slice-D production demonstrations yield usable source-backed outputs with measured cleanup savings. Each domain has explicit supported formats/limits and a reproducible acceptance fixture.

**Integration and rollback:** Adapters/skills can be disabled independently. Keep original inputs and previous artifact versions. Do not rerun external mutations when rolling a workflow or format adapter back.

**Chronicle and Git checkpoint:** append a `BE10` entry to root [buildjournal.md](buildjournal.md); branch `build/be10-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.



<a id="be11"></a>

### BE11 Version workflows templates and evaluated procedural learning

**Status:** planned. **Objective and value:** Implement F11/F18/F19 and U25: successful work can be reused without uncontrolled self-modification.

**Prerequisites:** BE07–BE10; automation triggers remain BE12. Memory references route per BE08.

**Files and ownership:** Existing: agent/learn_prompt.py, agent/background_review.py, tools/skills_tool_plugin.py, agent/curator*.py, skills/, optional-skills/, plugins/. Proposed: agent/workflow_contract.py, hermes_cli/workflows.py, workflow artifact/evaluation registry. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Represent a manual Workflow with typed inputs, parameter schema, steps/dependencies, output expectations, required capabilities, environment compatibility, version and run history. Conversational authoring/editing precedes a graphical canvas.
2. Extract a procedure from an accepted mission or demonstration into a draft. Distinguish stable steps from incidental coordinates, names and content; demonstration recordings retain consent/scope and are not executable authority.
3. Validate a draft with recorded tools or isolated dry-run accounts on varied inputs. Use draft→tested→approved→deprecated/revoked lifecycle, evaluation provenance and rollback predecessor. A skill cannot promote itself or expand its grants.
4. Keep template style/structure separate from procedure logic and memory preference pointers. A new template version does not rewrite old deliverables automatically.
5. Capture user corrections/failures as evidence. Keep held-out tasks apart from training or prompt tuning, measure against incumbent and generalist baseline, and require a human-authorized publication decision for private-derived sharing.
6. Execute workflows through the same mission, budget, capability, artifact and effect records. Store referenced canonical executable versions so resume/retry cannot silently switch to a changed recipe.
7. Coordinate optional memory-harness procedural discovery only for Ryoko and only as references to canonical workflow artifacts; specialists discover explicitly granted project skills without personal harness access.

**Interface and data contract:** WorkflowVersion {workflow_id, version, input_schema, steps, output_schema, capability_requirements, environment_manifest, provenance, evaluation_ref, state, predecessor}; WorkflowRun references one immutable version and a Mission.

**Failure and security boundary:** No hidden weight-training claim, self-promotion, permission inheritance from a successful demo or live duplicate mutation during A/B evaluation. Revoked versions cannot start new runs; existing effects still require reconciliation.

**Focused checks:** Focused: parameterized rerun with new input; demonstration generalizes beyond coordinates; revoked skill denial; capability escalation attempt; rollback restores content and metadata; private-source export denied without grant.

**Acceptance and exit:** P09 succeeds on a second varied input without rewriting instructions; approved procedures have reproducible evaluation evidence and a safe manual stop/review path.

**Integration and rollback:** Roll back the workflow pointer to an approved predecessor; preserve version/run records. Pause affected scheduled runs before revocation and reconcile their effects rather than deleting history.

**Chronicle and Git checkpoint:** append a `BE11` entry to root [buildjournal.md](buildjournal.md); branch `build/be11-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be12"></a>

### BE12 Make schedules monitors and commitments durable

**Status:** planned. **Objective and value:** Complete U17 and F08/F20/F21/F22/F23/F24/F33 with one owner of each schedule and honest monitoring health.

**Prerequisites:** BE02/BE03/BE06/BE09/BE11. Bring narrow occurrence identity/recovery earlier when any background task is enabled.

**Files and ownership:** Existing: cron/jobs.py, cron/scheduler.py, cron/scheduler_*.py, cron/executions.py, cron/delivery_queue.py, tools/cronjob_tools.py, hermes_cli/goals.py, gateway/delivery_ledger.py. Proposed: monitor/commitment records and typed schedule projection siblings. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Preserve tick locking, occurrence claims/execution logs and current delivery ambiguity safeguards. Define one canonical occurrence ID from schedule identity, version and scheduled time; persist claim generation, missed-run policy, timezone/DST semantics and recovery.
2. Correct outdated source assumptions: current cron agent construction passes skip_memory=False. Bind scheduled work to the intended agent identity and BE08 backend, not whichever profile-default memory happens to load.
3. Monitor records state the question, exact sources, meaningful-change predicate, cadence/trigger, expiry, budget, last success/error and notification policy. Establish a baseline; cosmetic changes do not alert. An inaccessible source is unhealthy, not unchanged.
4. Conditional tasks separate observing a condition from authority to execute the resulting action. Revalidate grant, target/input digest, budget and freshness at trigger time. Deduplicate event deliveries and bound retries/backoff.
5. Commitment extraction yields candidates with owner, outcome, source evidence and date uncertainty. Only accepted commitments become authoritative; preserve done/cancelled/waiting evidence and avoid reopening superseded obligations.
6. Calendar planning resolves current availability/timezone/recipient identity and previews changes. Correspondence distinguishes draft from sent and records exact recipients/content/effect receipts. Briefings query authoritative ready/waiting states rather than inventing work.
7. For F23, begin with one selected inbox/message source, explicit threads or bounded time range. Group related messages and classify actionable request / informational update / decision needed / waiting on, retain current participants/attachments, extract dates and flag any new promise in a draft. Evaluate missed requests, false urgency, stale facts and unintended commitments, not only draft fluency. F24 adds explicit waiting-on and weekly accepted-commitment review over one authoritative obligation record.
8. Define Hermes as schedule execution authority for later Dots. Retire or convert Dots Runner to transport/projection after import; do not let two schedulers fire the same occurrence. Old active/unknown occurrences must reconcile before migration.
9. Reconcile inherited gateway best-effort at-least-once delivery with cron’s unknown-send policy in one explicit adapter-capability matrix. Never erase the distinction by labeling all sends exactly-once.

10. Represent background memory/skill reviews as durable bounded jobs referencing immutable source/artifact versions, owning agent, purpose, budget and completion receipt. Specify script-only jobs separately from agent jobs, plus restart, clock-change, overlap, missed-run and expiry policy. Review jobs must not ingest a different version or inherit primary memory on recovery.

**Interface and data contract:** ScheduleSnapshot {schedule_id, version, owner_agent_id, timezone, trigger, next_due, policy, health}; Occurrence {schedule_id, version, due_at, occurrence_id, generation, mission_id, execution_state, delivery_state}; Monitor {source_set, predicate_version, baseline_ref, notify_policy, expires_at}; Commitment {owner, outcome, accepted, due_or_check_at, source_refs, state}.

**Failure and security boundary:** No duplicate firing after reboot/DST/webhook retry; no blind retry of dead claimed send; no inferred standing communication authority. Stops, revocation and expiry prevent future admissions while preserving already accepted effects.

**Focused checks:** Focused: tick/claim race; DST/missed occurrence; duplicate trigger; restart before/after acceptance; source outage; relevance versus cosmetic change; selected-inbox missed request/false urgency; weekly waiting-on review; pause/resume/revoke; immutable-version background review, script-only restart/clock/overlap; false commitment; failed delivery with completed execution; memory scope on cron.

**Acceptance and exit:** P10 detects controlled meaningful changes, suppresses cosmetic noise, exposes unhealthy checks and can be stopped. Accepted commitments and bounded conditions survive restart without duplicate external actions.

**Integration and rollback:** Pause schedules before changing ownership/version; import with deterministic IDs and an overlap-free cutover. Restore prior scheduler only after confirming no duplicate owner or unresolved occurrence.

**Chronicle and Git checkpoint:** append a `BE12` entry to root [buildjournal.md](buildjournal.md); branch `build/be12-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.


<a id="be13"></a>

### BE13 Give specialists durable handoffs and scoped executors

**Status:** planned. **Objective and value:** Complete U18 and F16/F17/F27/F29/F30/F31 while keeping one mission authority and separate individual memory.

**Prerequisites:** BE03/BE05/BE08/BE09/BE12; real executor capability checks precede routing.

**Files and ownership:** Existing: tools/delegate_tool.py, tools/delegate_tool_config.py, tools/async_delegation.py and siblings, tools/process_registry.py, tui_gateway/methods_subagents.py, tui_gateway/methods_voice.py, tools/environments/, gateway/platforms/. Proposed: agent/specialist_manifest.py, agent/delegation_contract.py, executor capability adapters. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Distinguish stable named specialist, ephemeral task child, execution process and UI identity. A specialist manifest defines responsibility, methods, default limits, output schema, tool grants and its built-in memory namespace. A UI display name does not change permissions.
2. Extend current durable async dispatch/completion ledgers and abandoned-owner recovery into resumable work contracts where safe. Do not describe process-local running child execution as already resumable; checkpoint only restorable state and mark unrecoverable tasks blocked.
3. Typed handoffs include objective, accepted artifacts/versions, evidence, constraints, deadline, budget reservation and narrowed capabilities. Use isolated workspaces; merge via BE07/BE11 conflict checks. Child completion receipt and parent delivery claim are distinct.
4. Parent owns synthesis, validation and user-facing status. Teams start only when bounded parallelism offers measured benefit; no default swarm or automatic escalation to more agents. A child cannot pass personal MCP access to a grandchild.
5. Device routing chooses among live authenticated executor capabilities, data locality, availability and cost. Losing a computer does not silently move private work to another host or report resumed execution before confirmation.
6. For F27, first register two authenticated bounded services such as transcription and document processing, with capability/version, health, input/output schema, size/time limits, destination and data-location policy. Execute a two-stage artifact pipeline using digests and transfer receipts; expose bytes/location before material transfer. Service loss yields pending/partial recovery at the affected stage without replaying completed downstream effects. Arbitrary remote shell is not the initial service abstraction.
7. For F16, implement push-to-talk, streaming transcript feedback and interruptible TTS via declared local/remote STT/TTS adapters; show processing location and unsupported media capabilities. Conversation/discussion becomes an accepted task only through the normal command contract, with decisive misheard names/numbers resolved. F17 starts screenshot/selected-window inspect and annotate, then guide/act modes for one supported workflow using fresh region references.
8. Voice/screen/channel ingress submits the same runtime commands. Separate stopping speech, stopping local UI streaming and cancelling a mission. Explicit observation/capture scope, current-frame freshness and consequential-action approval apply to screen tools.
9. Cross-channel handoff binds verified principal/project/agent/session mappings. Reconnect or a Slack/voice duplicate must resume the same mission; conversation history is a projection, not new instructions to replay.

10. Enforce configured maximum delegation depth and aggregate fan-out in addition to per-agent concurrency/budget. On parent crash, adopt a child only through a fenced authorized supervisor and preserved reservation; otherwise classify orphaned/partial/unknown state. Never respawn blindly or count durable completion recovery as resumed execution.

**Interface and data contract:** SpecialistManifest {agent_id, responsibility, methods_ref, builtin_memory_namespace, grants, output_contract}; Delegation {child_id, parent_run_id, immutable_handoff, reservation, workspace_ref, owner_generation, completion_receipt, result_delivery}; ExecutorCapabilities and IdentityBinding determine placement.

**Failure and security boundary:** No shared built-in memory directory, inherited primary credential, stale transport control, unbounded team costs, unauthorized executor migration or duplicate work on handoff. Abandoned child state is explicit.

**Focused checks:** Focused: specialist memory persistence versus ephemeral child isolation; child/grandchild narrowing and depth/fan-out; parent-crash adoption/orphaning and delivery claim; workspace conflict; steer/interrupt generation race; disconnected executor; two-service artifact transfer and endpoint loss; push-to-talk/STT/TTS capability and processing location; screenshot annotation/inspect-guide-act freshness; voice barge-in versus mission cancel; cross-channel duplicate input.

**Acceptance and exit:** One specialist and then a small team outperform the single-agent baseline on declared tasks without scope leakage, duplicated work or misleading completion. Handoff preserves the exact mission/artifact state.

**Integration and rollback:** Disable team admission and retain single-agent mode. Drain or pause children, preserve completion ledgers, revoke executor grants and keep namespaces bound to their original owners.

**Chronicle and Git checkpoint:** append a `BE13` entry to root [buildjournal.md](buildjournal.md); branch `build/be13-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.



<a id="be14"></a>

### BE14 Harden operations privacy provisioning and deployment

**Status:** planned. **Objective and value:** Complete U13/U28/U29/U34/U35; the minimum retention, telemetry and provisioning controls start in earlier phases before sensitive data is ingested.

**Prerequisites:** BE01/BE02/BE05/BE06 and the workload being certified. This is a consolidation gate, not permission to postpone privacy until the end.

**Files and ownership:** Existing: pm/, plugins/, plugin-catalog/, hermes_cli/observability/, hermes_cli/doctor*.py, hermes_cli/backup.py, hermes_cli/update_*.py, hermes_state_maintenance.py, gateway/status.py, gateway/shutdown_forensics.py. Proposed: typed redacted audit projection and bounded repair commands/runbooks. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Provision extensions outside ordinary discovery using PM, pinned artifacts/lock and immutable environment manifest. Record publisher/source digest and declared grants; discovery/startup must not silently install. Third-party personal-harness/LAYA plugins initially live outside core per repository policy.
2. Emit correlation-rich runtime/audit/performance events with redaction and purpose-specific retention. Separate mandatory recovery journal from optional analytics exports; opt-in raw capture and outbound telemetry remain explicit.
3. Define retention/encryption/key custody for conversations, summaries, agent memories, artifacts, logs, indexes, backups, provider copies and future training exports. Implement the chosen database/file/volume encryption with keys outside the protected store, controlled rotation and explicit key-loss/backup-restore fixtures before sensitive ingestion. Encryption at rest does not protect a compromised live process holding the key.
4. Implement deletion manifests and acknowledgments across derived indexes/cache, built-in individual stores, Ryoko harness operations actually supported, export copies and backup expiry. Preserve minimal non-payload structural tombstones when required; never promise remote deletion without proof.
5. Provide read-only inspect commands for ownership, waiting reason, budget, effect, delivery, memory backend, replay cursor, connection health and LAYA mode. Repairs require preview, affected IDs, scope validation and before/after journal events.
6. Offer bounded reconcile-effect/retry-delivery/rebuild-index/restore-checkpoint/revoke-lease actions using the same broker. There is no generic mark-success repair. A failed authoritative repair cannot silently target another profile.
7. Certify a portable local profile and daemon/client profile against explicit OS/executor guarantees. Keep SQLite until contention or multi-host requirements justify a separate U30 backend. Any PostgreSQL/fleet/federation experiment must prove semantic parity and operational need.
8. Produce backup/restore, upgrade/downgrade and disaster recovery drills with exact key/version requirements. Respect existing transactional updater and frozen compatibility surfaces.

9. Specify sink-failure semantics before treating events as authoritative: failure to persist required journal/effect-intent or mandatory audit state blocks unsafe dispatch and leaves an inspectable error. Optional analytics/export failure uses bounded buffering with explicit drop/backpressure policy and cannot hang execution. Use per-scope keyed correlation where needed; low-entropy hashes are not anonymization. Inject failures into mandatory persistence and optional sinks to prove both boundaries.

10. Make extension discovery metadata-only: do not import arbitrary plugin code, install dependencies or perform privileged/network probes merely to list a catalog. Activation happens through a verified manifest/grant and controlled lifecycle; test offline catalog reads and disabled-extension zero execution.

**Interface and data contract:** ExtensionManifest {digest, source, lock, capabilities, environment, revoked}; AuditProjection {correlation_ids, purpose, redacted_fields, retention_class}; DeletionManifest {record_refs, stores, requested, acknowledgments, limitations}; RepairPlan {actor, affected_ids, invariant_checks, before_revision, expected_after}.

**Failure and security boundary:** No secrets in logs/config inspection, silent SDK telemetry, unbounded journal growth or destructive repair bypass. Backup recovery is not automatically schema rollback. New distributed storage cannot weaken leases or effect semantics.

**Focused checks:** Focused: offline startup/no install; clean manifest reproduction; seeded export redaction; mandatory-journal failure blocks unsafe dispatch while optional sink outage stays bounded; record deletion across all supported copies; key rotation and restore; orphan/unknown/delivery-failure repair; wrong-profile repair denial; contention baseline.

**Acceptance and exit:** Operators can diagnose and repair supported failures without editing DB rows; retention/deletion claims are evidence-backed; supported deployments reproduce and publish their real guarantees.

**Integration and rollback:** Keep compatible backups and previous manifests; drain active work, snapshot all owning stores and validate readers before downgrade. Revoke a broken extension without invalidating unrelated agents.

**Chronicle and Git checkpoint:** append a `BE14` entry to root [buildjournal.md](buildjournal.md); branch `build/be14-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be15"></a>

### BE15 Introduce typed LAYA decisions and secure the Jetson node

**Status:** planned. **Objective and value:** Implement U36/U37/U41 in shadow first. This phase can begin alongside BE00–BE05 once its explicit prerequisites exist; it does not block the first useful artifact slice.

**Prerequisites:** BE01 contracts/config, BE02 receipts, BE03 wall-time budget, BE05 identity/egress and the narrow BE14 provisioning/retention controls. Hardware and serving facts from the supplied plan require live verification during implementation.

**Files and ownership:** Existing: hermes_cli/plugins.py, agent/auxiliary_client.py, hooks and observability seams identified by the source audit. Proposed: external laya-decisions plugin for Route A; agent/decisions/client.py and contract registry for later Route B; dedicated Jetson deployment manifests outside the core runtime. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Define DecisionClient.decide(point_id, state_packet, contract_version, deadline) and a versioned registry containing owner, closed questions/options, explicit unclear outcome, per-effect thresholds, compact-state builder, fixture set and fallback.
2. Modes are off, shadow, advisory and enforce independently per decision point. Route A observes real existing hooks without altering decisions. Core-only DP10/DP11 seams require Route B or a separately implemented observation hook; do not claim all catalog points are available through the no-fork plugin.
3. Provision a dedicated LAN node with verified Jetson/JetPack architecture, supported serving binary, pinned model and calibration digests, authenticated encrypted transport before private state packets (any explicitly accepted plaintext bring-up is synthetic/public shadow-only), allowlisted firewall, bounded queue/deadline and no payload logging. Do not copy insecure fleet defaults.
4. Begin with the Router/intention experiment selected by the user. Keep proposed LAYA-Guard and LAYA-Router model families separate if and when both are needed; they are reusable variants, not sixteen models. This is a non-generative typed service, not a chat-completions endpoint. An auxiliary provider:laya adapter accepts only classifier-shaped requests.
5. Health reports service/contract/model/calibration digests and queue/resource state without private packets. Benchmark PyTorch versus ONNX/TensorRT FP16 on actual hardware; verify answer parity before choosing speed.
6. Persist full decision distributions, state digest, mode, thresholds, route/fallback, latency and later label/outcome under redacted governance. Node failures use their own circuit/deadline and never trigger a main-model retry.
7. Calibrate per point, question type and option count using frozen domain holdouts. Measure accuracy, Brier, ECE, confidence AUROC, coverage at target precision, fallback and asymmetric error cost. No point enforces solely because a generic benchmark or sample-count target was met.

**Interface and data contract:** DecisionRequest {point_id, contract_version, state_packet, live_options, deadline, scope_digest}; DecisionResult {distribution, selected?, unclear, model_digest, calibration_digest, latency}; DecisionReceipt adds input digest, policy thresholds, mode, actual route, fallback and later outcome.

**Failure and security boundary:** LAYA cannot grant capabilities, create approvals, execute tools, enlarge budgets or override deterministic floors. Treat state packets as untrusted data. Timeout/invalid distribution/schema/model mismatch follows the declared safe fallback. Never log raw state by default.

**Focused checks:** Focused: contract validation/unknown option; unavailable node and bounded fallback; firewall/transport/auth; seeded log redaction; model swap digest; calibration reproducibility; shadow behavior equivalence. Record actual batch and end-to-end latency.

**Acceptance and exit:** Every enabled shadow point produces inspectable receipts with unchanged authority. Node failure is safe and bounded; verified hardware benchmarks replace all publisher/third-party performance assumptions.

**Integration and rollback:** Force each point off independently; restore prior checkpoint+contract+calibration as one bundle. Keep the incumbent deterministic/auxiliary path available throughout shadow and advisory rollout.

**Chronicle and Git checkpoint:** append a `BE15` entry to root [buildjournal.md](buildjournal.md); branch `build/be15-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be16"></a>

### BE16 Promote LAYA points individually and prove tool planning

**Status:** planned. **Objective and value:** Implement U38/U39 across all DP01–DP16 under U41. Promotion depends on decision-specific evidence, not a single global enable flag.

**Prerequisites:** BE15 plus the owning phase for each point in the DP matrix. BE08 memory isolation, BE12 monitor semantics and BE13 live specialist grants remain authoritative.

**Files and ownership:** Existing: gateway pre-dispatch hooks, tools/approval*.py, tools/cronjob_prompt_scan.py, tools/skills_guard.py, agent/turn_stop_gates.py, agent/background_review.py, agent/tool_guardrails.py, model_tools.py, tools/tool_search.py and context projection seams. Proposed: point-specific adapters/state builders under agent/decisions/, evaluated bundle selector. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Initial user-selected experiment: DP16/front-door intent using the existing intention-routing dataset after inspecting its actual schema/labels and training/evaluation suitability. The source suggested DP10/DP11/DP09 first; retain those as later candidates, not compulsory first work. Every point keeps its incumbent fallback until independently useful, sufficiently calibrated and explicitly promoted. No requirement to enable/train all sixteen before the agent is complete.
2. For the initial DP16 candidate, execute the future dataset→training/fine-tuning→calibration→frozen held-out evaluation→live shadow→qualified promotion chain. Inspect intention-label coverage first, add authorized synthetic tool-family/live-menu/unclear/reopen fixtures for gaps, and produce a versioned Router checkpoint with measured tool-need precision/recall, miss recovery, calibration and cache-aware task outcomes. Stop at shadow if gates fail; this is one bounded future model experiment, not permission to train now or a prerequisite for the rest of Hermes.
3. Guard initially tightens only DP06 risk, DP07 untrusted-content handling and DP12 outbound review. Deterministic hardlines run first. Relax only a proven-safe allowlisted class with explicit policy, calibration/red-team evidence and a user-visible control; low confidence escalates under the same egress/budget rules.
4. DP08 classifies only regex hits; unclear/instruction remains blocked. DP09 cannot override deterministic tests/receipts or declare a missing artifact complete. DP11 cannot send replies. DP15 chooses only from a live authorized specialist set.
5. DP16 uses a two-stage protocol: batch L1 tool need, L2 rough effort and L3a family yes/no questions; invoke conditional L3b choices/verification only for selected families. This reconciles the source’s one-call shorthand with its actual dependency graph.
6. Always retain an authorized tool_search/reopen escape path, including a no-tools decision. Reopen discovers/describes/invokes only allowed tools through the already-present bridge without rewriting the frozen schema prefix mid-conversation, and records a planner miss. Changing exposed schemas waits for a permitted new-context/compression boundary or the explicit tested ADR below; never rediscover personal MCP for a specialist. A tool-count bucket is a hint, not permission or a budget increase.
7. Honor the current conversation-prefix invariant. Default to selecting fixed bundles at a new session/task boundary represented by a new compatible prompt context, or a permitted compression boundary. Mid-conversation schema swapping is deferred unless an explicit ADR revises the invariant with full provider/cache/evaluation proof. Fixed bundles alone do not make swaps safe.
8. Use DP03 task-type routing only after paired value evidence; do not assume a classifier predicts difficulty. Charge LAYA/fallback wall time, measure first response and full mission success/cost including cache misses, bridge schemas and recovery.
9. Expose explanations and correction/override through FE12. Override the decision within existing policy, never the permission floor. Drift, false-allow, missed direct request, stale menu or tool-recovery failure triggers per-point rollback.

**Interface and data contract:** PointPolicy {point_id, mode, contract/model/calibration_versions, thresholds_by_class, allowed_effects, fallback, rollout_scope}; ToolPlan {need, effort_bucket, families, verified_tool_ids, live_catalog_version, bundle_id, reopen_policy}; decisions remain advisories to deterministic code.

**Failure and security boundary:** DMs/direct questions cannot be silently skipped; personal recall only for Ryoko. DP12 outage preserves existing outbound policy rather than bypassing it. Screening retains evidence with an untrusted wrapper or quarantine; never silently delete source content.

**Focused checks:** Focused: every DP fixture in the matrix; adversarial Guard input; wrong recipient; ambient versus direct ingress; held-out durable facts; no-tools false negative and successful reopen; stale tool menu; cache-prefix comparison; paired completion/cost/latency; circuit fallback.

**Acceptance and exit:** No point enforces without a recorded point-specific gate. Tool planner shows non-inferior task completion and measured net benefit including cache effects; needed authorized tools recover automatically with no permission leakage.

**Integration and rollback:** Revert one point to shadow/off or prior evaluated bundle without changing other points. Restore matching model/contract/calibration together; retain receipts for diagnosis and do not replay effects.

**Chronicle and Git checkpoint:** append a `BE16` entry to root [buildjournal.md](buildjournal.md); branch `build/be16-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.


<a id="be17"></a>

### BE17 Govern LAYA datasets fine tuning and model releases

**Status:** planned. **Objective and value:** Implement the optional U40 data/training lifecycle and sustain U41. The user-selected additional harness dataset is a post-agent-build task, not a blocker for core Hermes.

**Prerequisites:** BE14 data lifecycle and BE15 receipts. The additional DP05 harness dataset starts only after BE08 and the real agent integration are built. Explicit training destination/data approval is required for any external host.

**Files and ownership:** Proposed: private decision dataset tooling, redacting exporters, manifests, frozen holdouts, training runbooks and signed artifact registry; integrate with existing evals/ conventions rather than placing private datasets in this public repo. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. After the agent is built, design the additional Ryoko-harness recall dataset against the actual memory contract. Include no-recall/project/personal/allowed combination choices, forbidden specialist personal access, defer/unclear, stale/conflicting records, unavailable server, denied scope, correction and invalidation. Use synthetic/redacted authorized fixtures; separate train/calibration/held-out episodes. Do not export private harness data merely because this roadmap exists.
2. Inspect the existing intention-routing dataset before the initial DP16 experiment and record what it covers and misses. Intention labels do not prove tool selection, permission enforcement or calibrated harness recall; do not silently relabel one dataset as solving every DP. Training, calibration and promotion remain separate future tasks.
3. Label receipts from human decisions/overrides, independent teacher distributions, deterministic outcomes and approved synthetic/public rare cases. Never use LAYA’s unreviewed prior outputs as ground truth.
4. Deduplicate by source/episode, split train/calibration/frozen evaluation by task and time to avoid leakage, and retain provenance, consent/purpose, source deletion status and decision-contract version.
5. Build compact 320–768-token-style state packets as an initial source hypothesis, then measure actual serving limits and sufficiency. Preserve live menu order/identities and adversarial delimiters; truncation must not discard authority-critical facts.
6. Validate the reference RLCD recipe and license/toolchain before adapting to Apple silicon or a private GPU. The supplied four epochs/group four/sigma and LR schedule are starting settings, not guaranteed compatibility or best hyperparameters.
7. Produce ModelReleaseManifest with dataset/version/digests, software/hardware seed settings, contract/calibration versions, metrics/uncertainty, safety limitations and rollback predecessor. Define reproducibility as exact digest where deterministic, otherwise predeclared statistical metric tolerance.
8. Remove deleted records from future exports, invalidate affected dataset manifests and flag affected checkpoints for retraining/review; do not claim deletion unlearns existing weights. Signed/pinned artifacts deploy first in shadow.
9. Monitor drift using outcome metrics, fallback, overrides, confidence distributions and teacher agreement. Router drift can schedule a controlled retraining proposal; Guard drift triggers investigation and renewed red-team gates, never automatic privilege relaxation.

**Interface and data contract:** DatasetRecord {source_receipt, consent_purpose, scope, redacted_packet, independent_label, label_source, split, deletion_state}; ModelReleaseManifest {checkpoint_digest, dataset_manifest, code/runtime, contract_versions, calibration, metrics, signing_identity, predecessor}.

**Failure and security boundary:** No public raw training data, private-payload logs, unauthorized remote teachers/GPU, holdout contamination or self-label feedback loop. Model signing proves integrity/provenance, not safety.

**Focused checks:** Focused: seeded secret removal; deleted record absent next export; dataset split leakage; one small rebuild; calibration refit; malicious checkpoint/signature mismatch; incumbent/candidate shadow comparison; model+contract rollback.

**Acceptance and exit:** A governed build can reproduce the release within its declared tolerance, passes independent holdouts/red-team gates and can be rolled back. Training provenance and deletion limitations are honest.

**Integration and rollback:** Freeze new exports/training, revoke a release and restore its complete predecessor bundle. Preserve compliant manifests and minimal audit evidence while applying source deletion policy.

**Chronicle and Git checkpoint:** append a `BE17` entry to root [buildjournal.md](buildjournal.md); branch `build/be17-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="be18"></a>

### BE18 Run consolidated release gates and hand off to Dots

**Status:** planned. **Objective and value:** Close U32/U33/U35 with end-to-end evidence, then enable a separate ryoko-dots implementation using the agreed contracts.

**Prerequisites:** All phases required by the selected release slice and mandatory authority/effect/memory gates. First assemble the BE18 release candidate; FE13 then validates user journeys against that candidate; BE18 final sign-off consumes those receipts. Dots OD00 contract proof is required for the Dots-ready claim, not a prerequisite for producing the candidate. Deferred experimental features remain disabled and listed.

**Files and ownership:** Existing: scripts/run_tests.sh, workspace check scripts, CI classification, evals/, packaging/update infrastructure. Proposed: release manifest, compatibility fixtures and cross-repository integration runbook; Dots implementation belongs in TianJieHeng/ryoko-dots. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Run one consolidated relevant regression/contract/security/fault/migration suite against the final candidate, after small phase checks. Run full required project suites at release, not after every planning edit. Report every blocked/skipped/live-unverified area honestly.
2. Execute Slice A: project→Ryoko scoped recall→complete Markdown artifact→precise revision→template→later resume, with two conflicting projects and an unavailable harness. Repeat with a specialist using only built-in memory and granted artifacts.
3. Execute Slice B/C: bounded two-output mission, source-backed research, parameterized workflow, meaningful monitor; inject crash-after-acceptance, delivery failure, user steer, cancellation, stale approval and revoked access.
4. Certify supported OS/executor/provider combinations and backpressure/retention behavior. Repeat stochastic tasks with matched baseline/candidate configurations; publish cost/latency/success/review-effort variation, not just best demos.
5. Freeze RuntimeCapabilities, command/event/approval/effect/artifact contracts and version policy for the first Dots consumer. Export fixtures/TypeScript types without secret/internal provider state. Test disconnect/replay/snapshot/cancel and duplicate submission across the boundary.
6. Approve a separate Dots phase to replace DotAgent’s built-in generative loop with a Hermes AG-UI adapter at the shared Platform and Slack creation points; align voice/headless/schedule routes. OPENAI_BASE_URL is not this adapter.
7. Cut over one canary identity/project at a time with runtime_owner fencing. Never run both loops or both schedulers for one mission. Dots transport timeouts cannot define Hermes mission lifetime; delivery retry cannot restart a mission.
8. Finish release notes, recovery drill, data migration receipt and phase journal. Mark only demonstrated capabilities released; the rest remain planned, conditional or deferred in the coverage matrix.

**Interface and data contract:** ReleaseManifest {source_shas, dependency/config/schema_versions, supported_profiles, enabled_capabilities, evaluation_receipts, known_limits, rollback_bundle}; cross-repo compatibility version binds Hermes contract and Dots adapter commit.

**Failure and security boundary:** No production cutover with failed authority, unknown non-idempotent effect, cross-agent memory leak or unsupported migration. No unsupported success claim based on a green docs-only CI run.

**Focused checks:** Consolidated matrix at the end of this plan plus FE13; later Dots OD01–OD04 contract/migration tests. For this documentation change only, validate content/links/scope, not application behavior.

**Acceptance and exit:** The selected release demonstrably works end to end, recovery/rollback has been rehearsed, and a future Dots implementer can consume a stable boundary without inventing ownership.

**Integration and rollback:** Restore runtime_owner to the sole previous owner only after pausing admission, reconciling active effects and syncing committed projections. Do not replay old user prompts. Roll code/data/contracts together according to the tested release manifest.

**Chronicle and Git checkpoint:** append a `BE18` entry to root [buildjournal.md](buildjournal.md); branch `build/be18-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

## Complete engineering coverage U01 to U41

All are planned unless explicitly conditional. Existing mechanisms are preserved; the table maps the delta to an accountable phase. Source: engineering §§5–6 and §14.8, reconciled with product §10.2 and current user memory decisions.

| ID and source title | Backend phase | Consumer phase | Disposition and required delta |
|---|---|---|---|
| U01 Typed runtime contracts and dependency injection | BE01 | FE00 | Extend existing typed turn payloads with narrow scoped services |
| U02 Canonical session authority and fenced ownership | BE02 | FE04, FE14 | Extend current durable holder leases to all authoritative transitions/effects |
| U03 Durable event journal, checkpoints, and versioned replay | BE02 | FE04, FE14 | Versioned committed execution journal and checkpoints |
| U04 Structured concurrency and cancellation lifecycle | BE03 | FE04, FE10 | Structured ownership and honest cancellation |
| U05 Admission queues, backpressure, and resource limits | BE03 | FE04, FE06 | Durable bounded admission and fair resources |
| U06 Shared hierarchical budgets | BE03 | FE04, FE10 | Aggregate durable reservations beyond per-agent iteration caps |
| U07 Provider capability and protocol contracts | BE04 | FE01, FE07 | Lossless provider/external-loop capability contracts |
| U08 Unified retry, rate-limit, and failover controller | BE04 | FE01, FE04 | One bounded logical/physical attempt policy |
| U09 Capability broker and real execution isolation | BE05 | FE04, FE09 | Broker plus certified OS enforcement |
| U10 Scoped identity, secrets, and fail-closed authorization | BE01, BE05 | FE01, FE10 | Agent identity, exact approvals, fail-closed secrets |
| U11 Purpose-aware egress and verifiable local-only operation | BE05 | FE01, FE11 | Purpose-aware recipient and local-only contract |
| U12 MCP trust, sampling budgets, and complete refresh semantics | BE05 | FE01 | MCP grants, modern sampling scope, refresh/revocation |
| U13 Minimal core and reproducible extension provisioning | BE14; narrow BE15 prerequisite | FE01, FE11 | Reproducible offline extension provisioning |
| U14 Child workspaces and staged filesystem mutations | BE05, BE13 | FE03, FE09 | Isolated workspaces and reviewed staged promotion |
| U15 Effect journal and uncertain-outcome reconciliation | BE06 | FE04, FE08, FE09 | Effect intent and uncertain-outcome reconciliation |
| U16 Durable delivery and end-to-end receipts | BE06 | FE03, FE04, FE08 | Delivery truth separate from execution |
| U17 Durable scheduled jobs and background reviews | BE12 | FE06, FE08 | Durable schedules/occurrences/reviews |
| U18 Durable delegation and typed handoffs | BE13 | FE10 | Typed durable handoffs and completion; resumability added deliberately |
| U19 Transactional compaction and recovery fidelity | BE08 | FE02, FE03 | Preserve current transactional compaction/fidelity |
| U20 Typed anchors and an evidence ledger | BE07, BE08 | FE02, FE05 | Typed evidence/source/version anchors |
| U21 Prompt/cache contracts and a bounded fresh-memory segment | BE08, BE16 | FE02, FE12 | Stable prefix and bounded fresh corrections |
| U22 Structured memory with provenance, ACLs, and conflict handling | BE08 | FE02, FE10 | Ryoko personal MCP plus separate individual built-in memory |
| U23 Local hybrid lexical and semantic retrieval | BE08 conditional | FE02 | No duplicate Ryoko index; optional measured per-agent local retrieval only |
| U24 Composable memory capabilities and selective federation | BE08 conditional | FE02 | Backend capability interface; federation off by default |
| U25 Evaluated skill promotion and rollback | BE11 | FE03, FE06, FE07 | Evaluated versioned workflow/skill promotion |
| U26 Risk-based planning, human review, and verification | BE09 | FE04, FE05 | Risk-appropriate plans and deterministic verification |
| U27 Session-scoped tool exposure and availability | BE04, BE16 | FE01, FE12 | Scoped authorized discovery and cache-safe tool exposure |
| U28 Typed observability and privacy-separated audit projections | BE02, BE14 | FE04, FE11, FE12 | Typed correlation with private audit projections |
| U29 Retention, encryption, backup, and deletion closure | BE14; minimum from BE02/05 | FE11 | Retention/encryption/backup/deletion acknowledgment |
| U30 SessionStore abstraction and storage lifecycle | BE02, BE14 conditional scale | FE02 | SessionStore semantics with SQLite first |
| U31 Typed effective configuration and generated documentation | BE00, BE01 | FE01 | Typed effective config/provenance/generated reference |
| U32 Versioned runtime API and resumable event streams | BE02 early, BE18 full | FE00, FE14 | Narrow versioned durable API advanced for Dots |
| U33 Behavioral evaluations and fault-injection gates | BE00, BE18; every phase | FE13 | Behavior/fault/security/quality gates |
| U34 Operator inspection and repair workflows | BE14 | FE11 | Scoped inspect and previewed repair |
| U35 Narrow product scope and portable deployment profiles | BE00, BE14, BE18 | FE13, FE14 | Narrow portable certified profiles, no premature fleet |
| U36 Decision plane contract and versioned question registry | BE15 | FE12 | Versioned typed decision contracts/receipts |
| U37 Dedicated Jetson decision node | BE15 | FE11, FE12 | Dedicated secured measured Jetson service |
| U38 LAYA-Guard: safety decisions under deterministic floors | BE16 | FE04, FE12 | Guard under deterministic floors |
| U39 LAYA-Router: flow, routing and trigger decisions | BE16 | FE06, FE10, FE12 | Router per point including DP16 |
| U40 Decision data flywheel and fine-tuning pipeline | BE17 | FE12 | Governed data/training release lifecycle |
| U41 Decision calibration, shadow evaluation and promotion gates | BE15–BE17 | FE12, FE13 | Per-point calibration/shadow/promotion gates |

## LAYA catalog and optional rollout gates

Source: engineering §14.3, product §13. These are sixteen typed **decision points**, not sixteen separately trained models. Cover all contracts and fallbacks now; enable only useful evaluated points later. Initial intention-routing/DP16 work and the post-build DP05 harness dataset follow the latest user decision. Guard is optional later work, never a new permission authority.

| Point | Name | Owner and phase | Typed question/action | Required fallback and release boundary |
|---|---|---|---|---|
| DP01 | Ingress triage | BE03/BE15/BE16; gateway pre-dispatch | Router addressed choice + urgency/spam signals | Never skip DMs/direct questions. Only opt-in ambient not-for-agent above calibrated threshold may skip; fallback to current allow behavior |
| DP02 | Busy-input policy | BE03/BE16; current busy-input handling | Router interrupt / steer / queue | Default to configured policy; require explicit cancellation semantics and preserve late/missed-steer status |
| DP03 | Model tier and effort routing | BE04/BE16; provider/auxiliary routing | Router task-type choice: local/fast / main / higher effort | Fallback main model within grant. No difficulty/failure prediction assumption; privacy/cost/capability checked in code |
| DP04 | Skill and toolset preselection | BE04/BE11/BE16; skills and scoped tool catalog | Router ranks authorized candidates, chooses and verifies | Fallback authorized full/default view; embedding prefilter only if actually configured and permitted. Never expose Ryoko personal MCP to others |
| DP05 | Memory-harness recall gate | BE08/BE16/BE17; scoped recall adapter | Router needs prior context, then allowed none/project/personal/both options | Personal-harness options exist only for Ryoko. Others use own built-in context and granted artifacts. Existing safe recall fallback; additional harness dataset after agent build |
| DP06 | Tool-call risk gate | BE05/BE16; approval floors and smart approval | Guard destructive / irreversible / secret / outside-root / egress / intent signals | Code composes allow/ask/block after floors. Low confidence uses permitted current judge then human; never reduce mandatory confirmation |
| DP07 | Untrusted-content screening | BE05/BE16; transform_tool_result | Guard data / agent-directed instructions / mixed plus credential lure | Wrap/flag/quarantine with source retained. Fallback standard untrusted wrapper; screening never authorizes tools |
| DP08 | Prose-versus-directive scanning | BE05/BE11/BE16; cronjob_prompt_scan and skills_guard | Guard describes command / instructs execution / unclear on regex hits only | Fallback existing regex verdict. Instructs or unclear remains blocked; reduced false positives must preserve safety |
| DP09 | Done/verify gate | BE09/BE16; pre_verify/goal stop gates | Router verified done / continue / blocked / needs human | Deterministic validation, required test suite and receipts first; fallback current goal judge, parse failure safe pause |
| DP10 | Background-review trigger | BE08/BE11/BE16; background_review core seam | Router durable fact / preference correction / reusable procedure | Counter remains backstop. Route-B observation/consumer needed before enforcement; individual review cannot access primary personal harness |
| DP11 | Monitor and inbox scoring | BE12/BE16; monitor auxiliary/core seam | Router importance + notify now/digest/ignore + needs reply | Fallback current permitted auxiliary scorer. Never sends replies or invents commitments; measure missed important items as well as precision |
| DP12 | Outbound message check | BE06/BE16; output/send boundary | Guard credential / wrong recipient / cross-project private content | Hold for review, never auto-edit. Shadow outage preserves current deterministic send policy; mandatory guard mode requires explicit conservative hold/fallback |
| DP13 | No-progress detection | BE09/BE16; tool_guardrails | Router progress signal over bounded observations | Keep deterministic repeat guard; nudge/halt with usable partial result, never unbounded loop |
| DP14 | Anchor tagging for compaction | BE07/BE08/BE16; projection building | Router decision / constraint / approval / identifier / chatter, batched | Fallback existing exact/regex anchors. Tags do not delete evidence or override protected exact identifiers/approval bindings |
| DP15 | Specialist and kanban assignee routing | BE13/BE16; dispatcher/live roster | Router choice over live authorized agents | Fallback declared default or human if unclear. Responsibility fit never grants personal memory/tools or extra budget |
| DP16 | Front-door tool planner | BE04/BE08/BE16; get_tool_definitions and tool_search | Router need, effort bucket, family selection and live-tool verification | Default authorized toolset + main model. Always search/reopen escape; hints not hard cap. Initial intention dataset experiment; cache-safe boundary required |

### DP16 implementation contract and source conflict resolution

1. State includes compact current request, goal, relevant authorized context, live candidate menu and catalog version; do not send whole transcripts by default
2. L1 chooses no tools / needs tools / defer / unclear. L2 estimates 1 / 2–3 / 4+ / defer. L3a checks tool families. These independent questions can be batched
3. L3b selects live tools only for chosen families; verification that depends on a selected tool requires a subsequent step, or batch verification of all candidates. The source’s “all levels in one call” shorthand cannot remove causal dependencies
4. No-tools means no task-tool schema bundle, not removal of the always-available authorized search/reopen bridge. Resolve contradictory “no schemas (or bridge)” wording in favor of recoverability
5. A missed tool reopens the permitted catalog and records recovery. The bridge, direct dispatch and server credential checks all exclude Ryoko's personal MCP from other agents
6. Tool-count/tier suggestions do not increase limits, authorize a call or hard-cap the task. Budgets, allowed providers and deterministic policy still decide
7. Shadow may score each message, but schema exposure changes only at a permitted cache-safe boundary by default. Any different mid-conversation behavior needs an explicit ADR and provider/cache safety proof; fixed bundles alone are not proof
8. Measure no-tools precision, needed-tool recall, planner misses, recovery success, prompt/schema tokens, cache hit/miss cost, total latency and final task success with matched trials. Cost savings that lose necessary tools fail the gate

### Jetson and model release checklist

The supplied design proposes a dedicated **Jetson Orin Nano 8 GB / JetPack 6.2** node. Verify actual hardware, OS, serving support and model memory use before provisioning. No live fleet inspection or hardware benchmark was performed for these plans.

- Do not cohost OCR/translation on the decision node initially. Keep a larger NX 16 GB or warm spare conditional on measured need
- Start the selected Router experiment; host Guard too only when its future scope is ready. Verify actual `laya-serve` API/checkpoint compatibility before treating example endpoints as deployed facts
- Benchmark PyTorch against ONNX Runtime/TensorRT FP16; optional power/performance modes require thermal and answer-parity evidence. Proposed targets are ≤150 ms p95 end-to-end including LAN for interactive points and ≤1 s for background batches; they are not current measurements
- LAN only, no public/Cloudflare tunnel; allowlisted hosts, unique key-based SSH, rotated per-client credentials, disabled public docs/OpenAPI and authenticated encrypted transport before private state packets
- Pinned SHA-256/signed model artifacts with no boot-time online version resolution; health exposes actual model/contract/calibration digests and bounded queue state
- Metadata-only node logs, including request counts/errors/500s and latency/fallback metrics. The governed Hermes journal owns payload-bearing receipts; scrub payloads/keys from exception traces and service logs
- Compact packets, closed answer sets, explicit unclear and small hierarchical menus. Calibrate using the appropriate confidence signal, not `action.act_probability`; test label bias and use neutral two-option questions where needed
- Question wording, option count, model and calibration changes are behavior/API changes requiring renewed evaluation. Roll back model, contract, calibration and threshold policy together
- Use human/independent teacher/outcome labels, frozen holdouts and asymmetric false-allow metrics. A target such as 500 common-point shadow receipts is a starting collection goal, not a sufficiency theorem
- Train away from serving Jetson. Verify local Mac/MPS support first; private GPU requires approved redacted data and deletion policy. Public notebooks use synthetic/public material only
- Generative title writing, summaries, query rewriting, task decomposition, profile descriptions, TTS tags and MoA stay with suitable LLMs; counting, dates, arithmetic, API errors and authorization stay deterministic. Attribution/failure prediction is deferred without evidence

Route A observes existing hooks; Route B adds missing typed core consumers; Route C is a later decision-first refactor only where measured value warrants it. DP10/DP11 need their core seams before enforcement despite conflicting source roadmap ordering. Early node setup brings forward only the required reproducible provisioning/security; no full plugin overhaul is a hidden prerequisite. Provisional shadow receipts may exist before the canonical journal, but enforcing points require the durable contract.

## Engineering starter tickets T01 to T18

Each starter ticket is a bounded slice inside its owning phase, not an assertion of work completed.

| Ticket | Requested outcome under current scope | Completion evidence | Phase |
|---|---|---|---|
| T01 | Capture source/config identity and a representative fixture suite | Reproducible baseline with success/cost/latency outputs and documented limitations | BE00 |
| T02 | Add effective-config inspection with redacted provenance | CLI, gateway, and cron resolve equivalent intended policy | BE01 / FE01 |
| T03 | Remove unsanitized CUA environment fallback for privileged launch | Injected sanitizer failure denies launch without exposing ambient secrets | BE05 |
| T04 | Expand scoped-secret conformance matrix | Profile/user/tool/child combinations preserve authoritative misses | BE01 / BE05 / BE08 |
| T05 | Add root run-budget reservation wrapper around main and auxiliary calls | Concurrent requests cannot exceed the configured allocation policy | BE03 |
| T06 | Specify session generation and event/effect IDs | Concurrent submission and restart fixtures demonstrate one authoritative writer | BE02 |
| T07 | Implement unknown-effect state and one reconciliation adapter | Crash-after-acceptance scenario avoids duplicate mutation | BE06 |
| T08 | Separate execution completion from delivery completion in one adapter | Failed delivery is visible and retry does not rerun the agent | BE06 / FE04 |
| T09 | Test existing compaction fallback and handoff rehydration | Forced failures and multi-compaction restart retain consistent recovery pointers | BE08 |
| T10 | Implement or explicitly reject MCP prompt/resource refresh | Notification fixtures produce documented, scoped cache behavior | BE05 |
| T11 | Provision the Jetson with the selected Router checkpoint first, LAN-only authentication and firewall; add Guard only when its optional rollout is chosen | Health shows the loaded pinned digest; outside-allowlist scan fails; measured latency report. This narrows the source’s both-checkpoints ticket to the latest Router-first scope | BE15 optional |
| T12 | Create the `laya-decisions` plugin skeleton with `pre_tool_call` and `transform_tool_result` in shadow, writing receipts | Receipts carry contract version, model digest, distribution and fallback reason; Hermes behavior unchanged | BE15 optional |
| T13 | Write contracts DP06 and DP07 with fixture sets, including adversarial cases aimed at the classifier | Fixtures run in CI against the node or a CPU fallback | BE15–BE16 optional Guard |
| T14 | Capture `post_approval_response` choices as Guard labels through a redacting export | A training export exists without raw secrets; deletion of a seeded record propagates | BE17 optional |
| T15 | Write the DP10 background-review gate in shadow | Report on how many forks the gate would skip and whether any labeled durable fact would be missed | BE16 optional DP10 |
| T16 | Adapt the RLCD training script to a single Apple-silicon device, or document why not | One small fine-tune completes locally, or a private-GPU runbook exists with a redaction step | BE17 optional |
| T17 | Inspect/reuse the existing intention-routing dataset for the first DP16 experiment; add authorized synthetic gaps, then train/calibrate/evaluate and run log-only shadow. Private session-history export is not preapproved | Versioned candidate checkpoint plus held-out tool-need/set recall, no-tools precision, recovery and cache-aware cost/latency report; prompt behavior unchanged in shadow | BE16 / BE17 initial candidate |
| T18 | Behind a flag, assemble prompts from DP16 bundles with the `tool_search` bridge always present and automatic re-open on a planner miss | Paired evaluation: non-inferior task success, measured net token/latency change, planner-miss rate reported | BE16 after cache and promotion gate |

## Consolidated backend validation and release gates

These are future implementation checks. The present planning change does not install dependencies or run Hermes. Use the checked-out repository's current build instructions: prepare the independent test interpreter through PM, then **`scripts/run_tests.sh`**, never bare pytest. Python 3.14 is the documented development runtime; the broader pyproject range supports updater compatibility and is not a promise of equivalent runtime support. Node/npm versions come from the pinned package manifests.

For a focused change, select affected files, for example:

```bash
scripts/run_tests.sh tests/hermes_state/test_session_turn_lease.py tests/agent/test_turn_facade_lease.py
scripts/run_tests.sh tests/tui_gateway/test_projects_rpc.py tests/tui_gateway/test_session_control.py
scripts/run_tests.sh tests/tools/test_mcp_connect_secret_scope.py tests/tools/test_refresh_agent_mcp_tools.py
```

Use actual native OS lanes for OS behavior; do not patch `sys.platform` to fake an OS. I/O/profile/security changes require real imports and temporary homes, including A→B→A scope tests. JS behavior tests belong in the JS suite, not Python source-text assertions. Inspect existing tests and extend the invariant they cover; new test filenames below are not needed if an existing file is the right owner.

| Family | Existing starting evidence | Mandatory new/extended release scenario |
|---|---|---|
| Ownership | `tests/hermes_state/test_session_turn_lease.py`, `tests/agent/test_cross_process_turn_lease.py`, `tests/agent/test_turn_facade_lease.py` | Two workers, lease transfer, stale effect dispatch, accepted-command recovery |
| Compaction | `tests/hermes_state/test_compression_watermark_commit.py`, `tests/agent/test_compression_commit_fence_race.py` | Concurrent tail, forced summary failure, exact IDs, repeated restart, torn checkpoint |
| Budget/cancel | `tests/agent/test_run_budget.py`, `tests/agent/test_iteration_budget_race.py`, `tests/agent/test_provider_client_cancel.py`, `tests/agent/test_interrupt_propagation.py` | Durable reservation across parallel children/retries/auxiliary/MCP, stop during tool/human wait, remote uncertainty |
| Provider/retry | `tests/agent/test_provider_profile_recovery_seam.py`, `tests/agent/test_fallback_credential_isolation.py`, `tests/agent/test_fallback_api_mode_preservation.py` | Opaque state round trip, global attempt cap, account quota, unauthorized destination failover |
| MCP/credentials | `tests/tools/test_mcp_trust_gating.py`, `tests/tools/test_mcp_connect_secret_scope.py`, `tests/tools/test_refresh_agent_mcp_tools.py` | Same-profile primary versus child, direct/indirect invocation, malicious readOnlyHint, sampling/MRTR scope, refresh/reconnect/revocation |
| Isolation/workspace | Existing terminal/environment/approval suites | Seeded forbidden file/env/network access through shell/Python/plugin/MCP; sanitizer failure; symlink/stale-base staged promotion |
| Effects/delivery | `tests/gateway/test_delivery_ledger.py`, `tests/cron/test_delivery_queue.py` | Crash after remote acceptance, unknown non-idempotent action, duplicate input, attachment failure, delivery retry without execution |
| Schedules | `tests/cron/test_scheduled_occurrence.py` | DST, exact occurrence, overlapping claims, pause/revoke/expiry, source outage, changed predicate |
| Delegation | `tests/tools/test_async_delegation.py`, `tests/tools/test_async_delegation_orphan_sweep.py`, `tests/tools/test_async_delegation_stale_profile_scope.py` | Distinguish recovered completion from resumed execution; memory namespace, grant narrowing, parent delivery claim |
| Memory/context | Existing memory provider/store/session-search and profile tests | Ryoko-only personal server/credential across every route; each specialist/child built-in store; correction/deletion/outage; no implicit shared-memory export |
| API/client | `tests/tui_gateway/contracts/test_generated.py`, `tests/tui_gateway/test_tui_gateway_event_replay.py` | Durable restart cursor, snapshot/subscription race, duplicate command, stale approvals, slow consumer, version negotiation |
| Artifacts/domain | Existing file/format/eval suites plus BE10 fixtures | Complete valid bytes, exact revision/derivatives, viewer access, citations, calculations, lesson answer key, prompt-only generation stage |
| Privacy/operations | Existing backup/update/profile/observability tests | Seeded secret redaction, local-only packet capture, deletion across supported copies, key/backup recovery, authorized repairs |
| LAYA optional | New per-point fixtures under established eval conventions | Node outage, schema/digest mismatch, calibration, adversarial floor parity, DP16 miss/cache recovery, governed dataset deletion |
| Cross-repo | BE02 producer fixtures + later Dots adapter fixtures | Web/Slack/voice same mission, sole scheduler/loop, page conflicts, no global memory injection, rollout/rollback |

### Final campaign

1. Deterministic contract tests and behavior regressions on the exact final source state
2. Fault-injection at each durable intent/dispatch/acknowledgment/checkpoint/delivery boundary
3. Adversarial authority/egress/memory tests on each certified OS/executor; zero violations in the declared suite is a release gate, not a proof about all production traffic
4. Migration, backup/restore, key custody, index rebuild, replay retention and compatible downgrade drills
5. Bounded mixed-load tests: queue depth/age, RSS/disk growth, storage writer wait, interactive/background p50/p95, slow consumers
6. Repeated paired quality trials with same model/version where available, sampling settings, permissions, sources and resource limits; keep holdouts outside training/tuning and disclose live-provider drift
7. Source product pilot and journeys in FrontEnd_BuildPlan.md, plus one full required regression run before an implementation release. Retest affected behavior after subsequent fixes
8. Exact remote commit and relevant CI status, enabled capability manifest, unsupported/blocked cases and rollback receipt

Candidate source targets such as roughly one-second local cancellation, 20% fewer billed tokens, 20% better paraphrase recall, roughly one-minute resumption or 25% user-effort savings are **hypotheses** to set against measured baselines. They do not override correctness or apply to unsupported providers/executors. Preserve a graceful-handoff budget reserve. Billing estimates and learned image-token estimates are not vendor invoice guarantees.

## Decisions and explicitly deferred work

The build order, Ryoko-only personal harness and use of explicit agent identities are settled. The following implementation choices need an ADR or verified configuration before the dependent feature ships; they do not block publishing this plan.

| Decision | Required evidence / default | Owner phase |
|---|---|---|
| Stable primary/specialist/ephemeral identity scheme | User agrees identities will be configured; choose immutable IDs, ownership and lifecycle without invented names/tokens | BE01/BE08 |
| Actual personal-memory MCP contract | Server/tool/schema/transport, namespaces, consistency, limits, correction/delete/export acknowledgments and callbacks | BE08 |
| Individual built-in memory storage | Stable per-agent namespaces; ephemeral retention; session-search/background-review isolation; OS/broker proof | BE05/BE08/BE13 |
| Project artifact authority | Extend existing projects; one editable owner per item, compare-and-swap versions and explicit sharing | BE07; later Dots |
| First certified executor/OS/provider | Actual enforced capability/egress boundary, provider wire/usage/cancel contract | BE00/BE04/BE05 |
| Retention/encryption/key custody | Threat model and restore/deletion drill before sensitive ingestion; no secrets in repo | BE02/BE05/BE14 |
| API version/retention/cursor policy | Generated schema, durable snapshot+replay and compatibility fixtures | BE02/FE00 |
| Legacy delivery policy migration | Gateway at-least-once versus cron unknown-send; require durable intent before sensitive dispatch | BE06/BE12 |
| LAYA initial experiment | Inspect existing intention dataset; one useful DP16 candidate, not all points; later DP05 harness dataset after agent build | BE15–BE17 |
| Jetson availability/runtime | Verify source-proposed hardware/software, secure transport, measured memory/latency and licensed artifacts | BE15 |
| Dots page/learning/schedule migration | Single editable artifact owner; disable/reconcile global memory and duplicate learning/scheduling | FE14 / Dots OD phases |
| Conditional scale | Measured contention/multi-host need before PostgreSQL/fleet/queue/federation; preserve semantic parity | BE14/BE18 |

### Preserve or explicitly reject the old alternatives

- Preserve opaque provider state, stable prompt prefix, transactional compaction, exact anchors, intent/result ordering, scoped secrets, default-deny/hardline floors, existing cancellation and recovery machinery
- Keep separate concepts for durable personal/individual memory, historical session search, executable skills and shared project artifacts. U22–U24 blanket harness supersession is narrowed to Ryoko, not applied globally
- Do not build a graph/CRDT/vector/federation stack just to match the roadmap. Ryoko personal retrieval belongs to its selected harness; a measured per-agent local index is an optional separate decision
- Keep one deployable runtime and SQLite first. PostgreSQL, workflow services, replication/encryption products and fleet scheduling solve different needs; none makes external effects exactly once
- Reject one event loop per session as a default design, async-as-free-cancellation, shared kernel-as-sandbox, prompt classifier-as-authorization, tool hiding-as-security and hashes-as-anonymization
- Avoid an always-on planner/critic for every turn, automatic skill promotion from live duplicate mutations, forced manual approval for safe granted reads and compulsory Linux/Docker migration
- Do not infer that an auxiliary summary model must match the main context window; validate actual payload/overhead/output reserve and chunk when needed
- Do not copy a complete mutable tool registry per request; use shared immutable metadata and scoped views. Keep discovery separate from installation and invocation
- Defer broad connector marketplace, workflow canvas, automatic teams, continuous screen/audio capture, broad autonomous external transactions, cosmetic personas and unsolicited opportunity notifications
- Do not use a base LAYA checkpoint as an uncalibrated security judge, an OpenAI-compatible chat backend, a public tunneled service, or a hard cap that loses needed tools
- Keep generation with LLMs, exact arithmetic/dates/auth in code and LAYA as optional typed signal. Per-point optional training/evaluation is not a sixteen-model requirement

### Initial source discrepancies resolved in this pair

- Old 0.21.1 paths/claims are rebased to current facades/siblings and actual source; existing leases, transactional compaction, typed RPC/projects and MCP uncertainty are preserved
- Area docs drift on shared iteration budget, cron skip_memory and child concurrency; implementation/config readers win and must be repaired alongside relevant future changes
- U32 advances narrowly into BE02; the full client migration remains BE18/FE14, not the first task and not an excuse to defer durable ownership
- DP16 dependent stages cannot all run in one call; search/reopen survives no-tools; schema changes respect actual cache invariants
- DP10/DP11 require core seams before enforcement; U37 brings narrow provisioning/security forward; every enforcing point still requires U41 even if tightening only
- DP12 shadow fallback preserves existing policy, but any mandatory protective mode needs explicit conservative fallback; LAN alone does not encrypt private packets
- LAYA vendor/third-party figures and source hardware assumptions remain unmeasured; initial intention experiment and post-build harness dataset follow the latest user decisions

## Input manifest and planning verification

| Input | Bytes | SHA-256 | Coverage |
|---|---:|---|---|
| `upgrade(2).md` | 177625 | `5cf80a66952d7e0e56c7f5ea33a7fc9b24dd617c07a10a3e0d13c33e5bab9a35` | U01–U41, T01–T18, DP01–DP16 and architecture/rollout/evaluation |
| `user-facing-upgrades(2).md` | 151268 | `bc97f2e40adc020ac27684093c3ba5173610fc5704959eb72fe426e463c4512e` | F01–F35, P01–P16, journeys/product acceptance and memory revision |
| `upgrade.pdf` | 303105 | `d44bdb92381c6e417372bf3941eca11974001d244c6d6d7ea7118b23bceb7200` | 50 pages; substantive parity with engineering Markdown |
| `user-facing-upgrades.pdf` | 266457 | `5401c719121c7398ba97272902ce15cfb75c66a0610dadfff2d970788f938250` | 46 pages; substantive parity with product Markdown |

Planning verification comprises complete requirement-ID coverage, actual source-path checks, pinned-link checks, phase/dependency consistency, Markdown structure and a documentation-only diff. No implementation, model training, data export, dependency install, runtime test, connected-service test or deployment is claimed. Future progress and exact validation outcomes belong in [buildjournal.md](buildjournal.md).
