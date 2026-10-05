# Ryoko Build Journal

This shared journal records actual work on the Ryoko build. Planning is separate from implementation. Use [BackEnd_BuildPlan.md](BackEnd_BuildPlan.md) for BE00–BE18 and [FrontEnd_BuildPlan.md](FrontEnd_BuildPlan.md) for FE00–FE14.

## 2026-10-02 Planning and source review

**Status:** planning documents prepared; implementation has not started in this change.

**Reviewed baseline:** [b78931e3b0959c42dca7400c78a4dffd1bb48575](https://github.com/TianJieHeng/ryoko-agent/commit/b78931e3b0959c42dca7400c78a4dffd1bb48575). The supplied plans reference an older Hermes 0.21.1 snapshot; the build plans use this actual fork's current modules and existing mechanisms.

**Inputs reviewed:** `upgrade(2).md`, `user-facing-upgrades(2).md`, `upgrade.pdf` (50 pages), `user-facing-upgrades.pdf` (46 pages). Full text comparison found no substantive differences between each PDF and its Markdown companion.

**Documentation prepared:**

- Root `FrontEnd_BuildPlan.md`: 15 phased control/product work packages; F01–F35 and P01–P16 coverage
- Root `BackEnd_BuildPlan.md`: 19 phased runtime work packages; U01–U41, DP01–DP16 and T01–T18 coverage
- This requested root `buildjournal.md`
- Separate [ryoko-dots Integration_Handoff.md](https://github.com/TianJieHeng/ryoko-dots/blob/main/Integration_Handoff.md), based on [b01ac1f6a903e5e56c119d960901353ac0a3d171](https://github.com/TianJieHeng/ryoko-dots/commit/b01ac1f6a903e5e56c119d960901353ac0a3d171); Dots implementation remains later

**Decisions carried forward:**

- Custom Hermes first, OpenDots afterward in its separate repository
- Primary Ryoko alone uses the personal external MCP memory harness; other agents use isolated individual built-in memory; shared project artifacts are explicitly scoped
- Explicit agent identities will be configured; persistent specialists and ephemeral children need distinct memory lifecycles
- LAYA's sixteen decision points are a capability catalog, not sixteen mandatory models/datasets. Initial intention/DP16 experiment first; an additional Ryoko-harness decision dataset follows after the agent is built
- No private harness export, model training, server provisioning or application implementation is authorized by these planning documents

**Important source findings:** existing durable holder leases, transactional compaction, typed RPC contracts/projects, scoped MCP refresh and uncertain mutation handling should be extended. Current delegated children skip memory, so the desired individual child memory requires implementation. Existing profile isolation must be extended to per-agent personal-MCP enforcement.

**Verification for this change:** source and attachment review plus documentation coverage, path/link, structure and diff checks. Application/runtime/connected-service tests were not run because no application behavior was changed. After publication, record the exact repository commit and observed CI results here; publication is not established by this planning entry, and documentation checks do not establish runtime readiness.

**Next implementation checkpoint:** BE00 baseline/fixtures and FE00 contract inventory, then the mandatory identity/authority/durability foundations. Every implementation phase remains planned.

## Entry format for future work

Append one dated entry per meaningful checkpoint under its BE/FE phase ID:

- Status: planned, in progress, blocked, verifying, complete or deferred
- Objective and source IDs: U/F/DP/T/P requirements
- Base/head commit and branch/PR links
- Actual files changed and observed behavior, separate from intentions
- Contract/config/schema versions and migration/feature-flag state
- Tests or checks: exact command, result, evidence and blocked/not-run limits
- Security/permission, memory-scope, effect and delivery implications
- Integration and rollback evidence
- Remaining blocker/decision and next dependency

Do not mark a phase complete because a module exists or a plan was written. Record failed/flaky/skipped/mocked/live-unverified results honestly, and preserve user edits when updating this journal.

## 2026-10-02 BE00 — Reproducible evidence baseline

**Status:** complete as an evidence-baseline phase; this is not a green runtime or release certification. Source IDs: U33/U31/U35, T01. No runtime behavior, dependencies, production configuration or deployment changed.

**Base:** `4b7268c69f72c3fa5d2d056a3bbce9a3b65d94cd` (planning), preserving runtime baseline `b78931e3b0959c42dca7400c78a4dffd1bb48575`. Checkpoint branch: [`build/be00-baseline`](https://github.com/TianJieHeng/ryoko-agent/tree/build/be00-baseline). The checkpoint is the commit containing this entry; remote commit/CI verification is reported with the phase receipt, not assumed here.

**Delivered:** `docs/build/baseline.md`, the decision/ticket register in `docs/build/decisions/`, measured `docs/build/baseline-manifest.json`, the standard-library `scripts/ryoko_baseline.py` reproducer, and two behavior tests in `tests/scripts/test_ryoko_baseline.py`. Baseline manifest/fixture versions are 1. The helper reads checked-in dependencies plus synthetic configuration, never operator secrets/config. It delegates execution to the required runner; by default every fixture is explicitly unmeasured.

**Environment:** isolated PM build, Python 3.14.7, pytest 9.1.1, Linux x86_64. `python -m pm.build_env --source . --out .venv --group dev --group test` completed with separate disposable HERMES_HOME/HERMES_RUNTIME_DIR. PM offline lock verification passed. `HERMES_PYTHON="$PWD/.venv/bin/python" scripts/run_tests.sh tests/test_hermes_yaml.py` passed 9 smoke tests. No lock or dependency file changed.

**Focused campaign:** with disposable home/runtime and the same explicit interpreter, `scripts/run_tests.sh tests/scripts/test_ryoko_baseline.py tests/agent/test_secret_scope.py tests/tui_gateway/test_projects_rpc.py tests/agent/test_terminal_approval_batch.py tests/agent/test_fallback_429_after_timeout.py tests/hermes_state/test_session_turn_lease.py tests/cron/test_delivery_queue.py tests/hermes_state/test_compression_watermark_commit.py tests/agent/test_memory_provider.py -j 3` finished 9 files in 19.3 seconds: **174 passed, 2 failed**. The existing `test_projects_reads_are_scoped_to_the_requested_profile` and `test_projects_tree_is_scoped_to_the_requested_profile` assertions see an additional `tmp` project-tree node. Both fail on the unmodified runtime; retain this inherited evidence for BE07 diagnosis. They are not described as passing, fixed or harmless. Manifest records counts and the campaign-log digest; raw host/provider traces are not published. `git diff --check` passed. The two new baseline-helper tests passed.

**Boundaries and rollback:** no migration or new runtime feature flag enabled. Primary personal MCP, per-agent enforcement and LAYA remain unconfigured/disabled targets. Existing profile fixtures do not prove same-profile agent isolation. Provider behavior is mocked; real external effects, credentials, service delivery, hostile-code executor boundaries, hardware performance and full regression suite are not tested. Revert this evidence tooling/documentation independently; retain measured failures. No cost/latency/success benefit is claimed from fixture execution time.

**Next:** BE01 trusted identity, immutable effective configuration and construction-scope equivalence. Runtime implementation remains pending; actual identity IDs and personal harness contract must come from explicit configuration, never display names or invented credentials.

## 2026-10-02 BE01 — Trusted identity and scoped runtime authority

**Status:** complete for the opt-in identity/configuration foundation, locally verified; downstream executor isolation, automatic per-agent memory and live services remain explicit later-phase gates. Source IDs: U01/U10/U31/U28, T02/T04. Base `91498070a98f40975a76822e86c8076e30ffb918`; checkpoint branch [`build/be01-identity`](https://github.com/TianJieHeng/ryoko-agent/tree/build/be01-identity). The checkpoint is the commit containing this entry; the phase receipt verifies its exact remote SHA and journal bytes.

**Implemented:** immutable version-1 `IdentityBinding`, `AgentPolicy`, `AgentContext`; trusted optional `agent_identity` config with generated reference; common constructor/turn/teardown binding across CLI/TUI/gateway/API/cron; distinct narrowed ephemeral children; atomic single-assignment session identity on the existing SessionDB; read-only `AIAgent.runtime_context`; redacted `hermes config identity [--agent ID] [--session ID]`. Missing, invalid, unknown-version, stale or cross-home identity fails before provider setup. Empty new session placeholders may bind once; historical unbound sessions require explicit migration. Removing policy cannot silently downgrade a persisted bound session.

**Enforcement:** exact secret grants and authoritative scoped misses (including caller-default and raw-environment fallback); final child-env filtering after managed/explicit overrides; direct-provider credential validation; schema, registry, bridge, inline and hook dispatch gates; MCP admission before credential interpolation, connection ownership, direct/utility RPC, reconnect and adoption checks. The actual unsafe `skip_memory=True` + explicit memory-toolset exception is closed under identity mode. Cache-parity background forks/standalone curator are explicitly unavailable until they have isolated owners. Full implementation boundaries and rollout instructions: [identity-runtime.md](docs/build/identity-runtime.md). Field reference: [agent-identity-reference.md](docs/build/agent-identity-reference.md).

**Files:** new identity/lifecycle/context modules and CLI inspection; integrated `agent/agent_init.py`, `turn_context.py`, `turn_facade.py`, `tool_executor.py`, `secret_scope.py`, background review/curator, `run_agent.py`, config/parser/defaults, SessionDB session operations, `model_tools.py`, delegation/env passthrough/local child environments, tool registry and MCP config/discovery/handlers/refresh/registration/reconnect. Added focused behavioral tests and generated-reference tooling. Optional Weixin credential reads tolerate a denied value without an attribute error. No dependency, database schema version, actual identity, credential, OS/network configuration or production environment changed.

**Validation:** exact final 19-file canonical-runner command and per-file counts in [be01-validation.json](docs/build/be01-validation.json): **240 passed, 0 failed**, 16.7 seconds, Python 3.14.7 / pytest 9.1.1, isolated homes and three workers. Includes real config/SessionDB/profile A→B→A, same-profile primary/specialist/child, real harmless subprocess credential canaries, forbidden hidden/direct MCP and utility dispatch, owner/reconnect mismatch, hook/frozen-context/once-dispatch, constructor parity and redacted CLI tests. Focused Ruff, generated reference freshness and `git diff --check` passed. Wider worker regressions also passed, but overlap means their counts are not added to the final campaign. Two existing execute_code tests are blocked before spawn by AF_UNIX socket `PermissionError` even after an escalated retry; five native-platform tests were skipped. These are not recorded as green. BE00's two inherited project-tree failures remain open for BE07. Full regression/live provider/server/hardware tests were not run.

**Configuration/rollout/rollback:** schema and binding revision 1; absent/empty policy preserves legacy behavior without claiming new isolation. Explicit malformed policy never falls back. Nonempty project/egress grants are rejected pending their enforcement; opaque provider pools are unsupported pending BE04. Strict nonprimary generic code/filesystem/browser/plugin/MCP execution is denied as unsupported until BE05; this is not an OS sandbox claim. Strict automatic memory is visibly unavailable until BE08 and never silently uses shared built-in personal memory. Policy changes require new sessions or explicit reviewed migration. Rollback to a pre-BE01 binary against identity-bound sessions is unsupported; retain the enforcing code/config pair, bindings and transcripts rather than erasing authority history.

**Next dependency:** BE02 durable command/event/checkpoint semantics and generation fencing on existing ownership/storage/contracts. Actual configured IDs and personal-harness service contract remain user deployment inputs; none were fabricated.

## 2026-10-02 BE02 — Durable commands, owner generations and runtime API

**Status:** complete for the versioned durable-runtime foundation, locally verified. Source IDs: U02/U03/U30/U32/U28, T06. Base: `ee1e2af072e19152c16158d29cbed26a23c4fe5f`. **Publication correction:** the user explicitly requested direct implementation on `main`, without pull requests. BE00/BE01 were safely fast-forwarded to `main`; BE02 and subsequent phases publish commits directly there after verification, without a PR step. No force push or overwrite of concurrent work is authorized.

**Implemented:** additive SessionDB schema **32**, runtime envelope **1**. The existing compression-root session owns a monotonic generation counter; its existing turn lease carries the fence. Atomic acceptance/idempotency/conflicts, single execution claims, bounded typed model/tool outcomes, checkpoint CAS and consistent restart-durable snapshot/replay use the existing SQLite writer. Expired/high/foreign cursors return `snapshot_required`; unknown schema or missing retained events fail closed. Retention advances a durable floor. Controls and pending submissions do not overwrite the active run with a misleading completion state.

**Actual execution:** the existing AIAgent loop consumes accepted commands after its sole lease admission. Provider/tool dispatch rechecks ownership, including physical callback paths. A dead claimed operation is never blindly rerun; exact completed retries use recorded results. Main/auxiliary invocation gates refuse unsupported native/plugin inner loops before execution. The real TUI worker receives an explicit receipt across its raw-thread boundary. Steer/cancel records mean queued/requested, never provider cancellation or effect reversal. Approval/effect protocol remains gated for BE05/BE06. Started operations without recorded outcomes remain visible as bounded pending/uncertain references through restart/checkpoint/pruning; recorded invocation completion does not certify an external mutation.

**API:** `runtime.capabilities`, `runtime.command`, `runtime.snapshot`, `runtime.events.since`, declared in Python and regenerated into shared TypeScript/OpenRPC. Every new method checks actual transport ownership, profile, immutable identity and stored binding; client actor claims are forbidden. Safe projections omit raw model/provider/tool payloads. Existing token-ring replay remains separate. [Durable runtime contract](docs/build/durable-runtime.md) documents boundaries, retention, recovery and unsupported transports.

**Integration corrections:** compressed physical-tip reconstruction preserves the immutable logical identity only after proving the existing canonical lineage; a copied branch record remains denied. Early identity claims now preserve later constructor/delegate metadata, and strict child fork markers persist before lease admission. Strict children cannot inherit an opaque parent credential pool after construction. Legacy duck-typed parent fixtures do not manufacture authority from a fabricated attribute. The existing delegation path test now compares actual database-file identity rather than `/tmp` versus `/var/tmp` spelling, matching its documented invariant. A final RPC test fixture was corrected to supply the operation ID required by the real coordinator's event contract.

**Validation:** [be02-validation.json](docs/build/be02-validation.json) records the final settled-source five-file integration campaign: **77 passed, 0 failed**, 8.9 seconds, no file retries. Includes the real agent loop with two provider calls and one harmless tool executed once, duplicate read-only recovery, stale-owner denial, journal failure, raw TUI thread, recovered old control refusing a successor, full reconstructed compressed-tip continuation, strict identity and transport/store RPC boundaries. Earlier bounded campaigns: state directory **1492 passed, 0 failed, 16 native-platform skips** (146 files, 53.4s); core/provider/lease/TUI/registry **218 passed, 0 failed, 4 skips** (15 files, 15.6s); delegation/identity **116 passed, 0 failed** (4 files, 12.0s). Counts overlap and must not be added. The final store projection change was retested in the final 77-test campaign, not mislabeled as covered by the earlier full state run. Shared TypeScript typecheck, standalone generated-contract compile, generated freshness/catalog, focused Ruff and diff checks passed. Locked Node test/shared dependencies were installed without lifecycle scripts or manifest/lock changes.

**Limits and rollback:** deterministic provider/tool doubles are not live API, billing, memory-harness or hardware measurements. Full project regression remains BE18. OS/executor isolation, encryption, durable external-effect/delivery reconciliation and deployment certification remain their mandatory owning gates. BE00's project-tree failures and earlier AF_UNIX environment limitations remain explicitly recorded. Drain writers and retain compatible enforcing code/config/backups before downgrade; do not delete idempotency history, reset owner generations or replay uncertain calls. No production service, secret, user identity, model training or Dots code changed.

**Next:** BE03 shared durable budgets, bounded admission and cancellation/resource lifecycle, extending these committed ownership and command records.

## 2026-10-02 BE03 — Shared budgets, bounded admission and honest cancellation

**Status:** implemented and locally verified for the declared opt-in budget adapter and durable runtime-command admission surface. Source IDs: U04/U05/U06. Base: `85001fc450cbeaa3615ede9a610f59cfa6841db2`. Publication is directly to `main`; the checkpoint containing this entry is verified remotely in the phase receipt. No PR, force push, credential, live deployment or production configuration change.

**Durable accounting:** additive SessionDB schema **33**. Parent/child accounts atomically reserve against every ancestor for tokens, attempts, wall allocation, provider/executor slots and optional integer-micro-unit cost. Actual run/lease identity fences each leaf. Trusted child bindings and the original session root prevent new turns, retries, restart or child fan-out from resetting ceilings. Accepted commands persist their canonical policy in the same transaction, including direct CLI/headless acceptance before a first root exists; a resumed accepted command cannot acquire a widened, disabled or newly enabled policy. Queue acceptance pins the absolute deadline as well. Existing local iteration limits remain enforced.

**Physical integration:** main and auxiliary inference use the real request seam with exact route validation, conservative whole-envelope input bounds, enforced output caps, request/root deadlines and SDK retries disabled. Application retries reserve separately. Cost mode requires operator-verified worst-case rate contracts; token mode explicitly leaves cost untracked. Integer ceiling arithmetic remains exact above 2^53. Unknown usage stays reserved, and a timed-out remote request retains its provider slot until genuine reconciliation; observed overrun becomes debt rather than a refund. Real trusted parallel children share the same aggregate root. Immutable configuration is documented in the generated [runtime-budget-reference.md](docs/build/runtime-budget-reference.md).

**Admission and cancellation:** bounded per-profile-store queues reference the existing command journal and feed the existing TUI worker pipeline. Atomic acceptance, per-principal fairness, finite interactive priority, active/queued/payload/disk limits, expiry, cancellation and recovery are tested. Startup maintenance can expire unattached work without another submit; only authenticated live sessions may launch. Claimed uncertainty is never replayed. Shutdown fences this process before teardown, terminalizes its unclaimed owned work and leaves unrelated accepted work recoverable. Queue/journal API snapshots share one read snapshot; reads do not execute or expire jobs. Task scopes propagate stop to provider, human wait, tool, child and registry-owned process hooks, preserving earlier same-owner and foreign processes. Known pending handles remain visible; local completion does not invent upstream acknowledgment or effect reversal. Partial output is retained.

**Scope and boundaries:** budget policy is absent/empty by default for new unbound sessions; established roots cannot be disabled. The first certified request adapter is a text-only single OpenAI SDK chat completion; only local todo handling and trusted delegation currently have tool budget contracts. Unsupported provider inner loops, media, embeddings, MCP sampling, memory-service calls, LAYA calls and callable plugin hooks/middleware/events fail closed under enabled budgets. Plugin rejection cannot silently skip permission guards or kill the shared event worker. Queue priority supports background entries, but this phase does not claim a separate cron-queue migration or a global cross-profile queue. Conservative claimed/remote-unknown work may hold capacity pending reconciliation. [bounded-runtime.md](docs/build/bounded-runtime.md) documents contracts and rollback.

**Validation:** the final settled-source 13-file campaign passed **153 tests, 0 failed, 0 skipped**, in 10.1 seconds with five workers and no file retries. Exact commands, per-file counts and log digests are in [be03-validation.json](docs/build/be03-validation.json). Provider responses use deterministic HTTP transports through the real OpenAI SDK, not live API calls. Real SQLite concurrent writers, AIAgent construction, profile/transport ownership, subprocess cleanup, child delegation and restart/migration paths are exercised. Broader bounded regressions passed: 202 tests with 4 platform skips across existing streaming/interrupt/delegation/TUI admission; 115 plugin/runtime tests; 71 admission/cancellation/control tests. Counts overlap and are not added. Shared TypeScript typecheck, generated contract/reference freshness, focused Ruff and diff checks passed. Full project regression remains BE18; inherited BE00 project-tree failures and the earlier AF_UNIX environment limitation remain recorded, not relabeled as fixed.

**Rollback and remaining gates:** preserve schema-compatible readers, accepted policies, root bindings, spent units and unknown reservations. Stop admission and drain/terminalize accepted work rather than erase ceilings or replay uncertain calls. No vendor-invoice, remote-cancellation, hostile-code sandbox, hardware or deployment certification is claimed. BE04 owns provider capability/attempt/failover and authorized tool-view contracts; BE05 owns certified execution isolation; later phases own external reconciliation, memory and mission lifecycle.

## 2026-10-02 BE04 — Provider contracts, finite attempts and authorized tool views

**Status:** implemented and locally verified for the declared adapter/policy boundaries. Source IDs: U07/U08/U27. Base: `8e3a567b94152a4c7a5ece992f001acf6698cf68`. Publication is directly to `main`; the phase receipt verifies the commit containing this entry and its journal bytes. No PR, live provider, credential configuration, dependency update, deployment or training.

**Protocol fidelity:** recognized provider adapters now declare streaming, parallel tools, media, usage, cache semantics, cancellation, opaque-state version and execution ownership. Model support remains explicitly unverified. Unknown clients and external-agent inner loops cannot gain durable execution by claiming a familiar API mode. Additive schema **34** introduces nullable versioned `provider_sidecar` for previously unrepresented ordered Anthropic/Bedrock state, leaving signed reasoning/Codex columns and `api_content` unchanged. Native blocks, signatures, tool IDs, continuation state and media survive flush/reopen/repair/export/import and actual gateway/CLI/TUI branch writers. Multipart media now retains its established JSON representation instead of irreversible screenshot-label flattening. Display projections omit private sidecars. Actual lease admission validates active replay state, so even empty supplied resume history cannot bypass an unknown future version; refusal releases the lease and leaves stored bytes unchanged.

**Attempts and clients:** every certified physical request has an Attempt correlated to its BE03 reservation and safe durable event. Auth, quota, throttling, overload, context overflow, unsupported capability, refusal and ambiguous transport failures are distinct. Finite retries require evidenced rejection, honor Retry-After as a floor and cannot enter independent legacy recovery ladders. Ambiguous 503/timeouts stop with retained uncertainty. HTTP errors without usage receipts retain unknown token/cost maxima; a completed refusal may release its provider slot but does not certify free billing. Context overflow is terminal in the initial bounded policy. Account-key circuits are bounded **process-local** cooldowns; durable budgets remain the spending authority across restart. Client/transport caches include identity/configuration/secret scope, endpoint, proxy/TLS and event-loop ownership. Shared pool entry and main/auxiliary fallback/recovery paths prevent strict identities from loading ambient opaque pools, refreshing OAuth or changing recipients through a later-denied route. Scoped single-key execution remains available. Automatic strict model/recipient fallback stays unsupported until purpose/recipient/continuation grants are enforceable; configuration or key availability alone is not authority.

**Tools and API:** shared catalog metadata is immutable; per-session ToolViews distinguish installed/authorized/discoverable/selected tools and safe availability codes. Catalog digests include only authorized metadata. Search/describe/call, connector results, direct invocation and reopened schemas apply the same grants. Denied personal MCP names no longer leak through unavailable-source summaries. Authorized tools without bridge grants retain direct schemas instead of disappearing behind inaccessible discovery. Construction finalizes the actual granted schemas after memory/context-engine injections; existing atomic refresh/prefix-restore boundaries update views without mid-turn reprobes. Legacy cache behavior is preserved, strict same-profile identities do not share availability decisions, and returned nested schemas cannot mutate the cached prefix. `runtime.capabilities` exposes typed safe provider declarations and the stored matching-policy ToolView; event replay exposes only validated physical-attempt correlations, never raw endpoints/keys/provider payloads. Python contracts and shared TypeScript/OpenRPC are regenerated.

**Validation:** final frozen-source campaign **229 passed, 0 failed, 0 skipped**, across 19 files in 20.5 seconds, five workers, no file retries. Exact commands, per-file counts and log digests are recorded in [be04-validation.json](docs/build/be04-validation.json). Targeted real SQLite/SDK/transport tests cover protocol round-trips, unknown-version dispatch refusal, same-profile A→B→A grants, direct/bridge denial, late injected-schema authorization, Retry-After/circuit/attempt ceilings, async uncertainty, pool isolation and safe API inspection. Additional bounded campaigns include **308 passing** budget/auxiliary/credential-pool tests, **240 passing** tool/cache/refresh tests, and **335 passing** storage regressions; the one old multipart-flattening assertion was deliberately corrected and its entire incremental-persistence file passed afterward. Counts overlap and are not added. Temporary concurrent-edit import failures were rerun after source coherence; none are presented as passing without that rerun. A mocked OAuth fixture was isolated to `tmp_path` after its old real-home lock path hit the read-only filesystem; no actual auth code or account changed. Shared TypeScript typecheck, generated freshness, focused Ruff and diff checks passed.

**Boundaries and rollback:** [provider-and-tool-contracts.md](docs/build/provider-and-tool-contracts.md) records rollout and limitations. Attempt/cost certification remains the BE03 operator-verified OpenAI text adapter; opaque plugins/providers/media billing are not certified. No live billing, provider cancellation acknowledgment, hostile-code confinement or hardware performance claim. Keep compatible schema/sidecar readers and authority history; never discard native state or move private context to an incompatible recipient to make fallback appear successful. Full regression remains BE18; previously recorded inherited/environment limitations remain open. **Next:** BE05 actual tool/executor/approval/MCP/egress boundaries.

## 2026-10-02 BE05 — Enforced capabilities, isolation, recipient plans and MCP trust

**Status:** implemented and locally verified for explicitly certified adapters, with unsupported modes denied. Source IDs: U09/U10/U11/U12/U14. Base: `f7e4b6fa8e7fc69e496077d2e9bd7a70aba0ccca`. Direct `main` checkpoint; the phase receipt verifies the commit containing this entry and journal bytes. Existing schema **34** remains unchanged. No dependency installation, host-global security/network change, credential/account configuration, live service, deployment, training or personal-data export.

**Deterministic authority:** short-lived one-use capabilities bind the trusted principal/agent/run, current owner generation, immutable live policy, exact input/schema/handler digest, operation class, resource roots, destination purpose and expiry. Policy is checked before model guardrails/hooks and again immediately at registry or inline execution. Exact human approval uses the existing UI but cannot inherit session/always/yolo permission; absent UI, changed/expired/replayed approval or revoked ownership denies. These are process-local dispatch tickets, not fabricated durable effect receipts; BE06 owns outcome/reconciliation persistence. Uncertified opaque tools and plugin callbacks remain denied even for a primary strict identity.

**Real executor boundary:** configured identities route actual `execute_code` calls through a Linux x86-64 stateless Python adapter using private user/mount/network/PID namespaces, a private chroot, read-only runtime/input mounts, dropped capabilities, no-new-privileges and a native seccomp allowlist. It has no host home/repository/proc/device/socket exposure, ambient credentials, network, process spawning, nested tool RPC or third-party packages. Launch must prove setup before untrusted code starts; unsupported OS/backend/enforcement never falls back to the host kernel. Real generated native-syscall fixtures prove host file/env/process inspection, symlink, socket and fork/exec attacks fail. Finite CPU/address-space/file/tmpfs/log/deadline limits and process-group cancellation are tested. The actual registry→live SQLite owner→broker→budget→OS-worker path reserves at the launch edge, including direct calls; unacknowledged child termination retains its slot/usage uncertainty. Context-specific tool descriptions match the stateless supported envelope without mutating the shared catalog.

**Staging:** immutable input and output manifests retain base revision, exact membership and hashes. Only bounded regular one-link outputs are exported after worker termination; symlinks/devices/hardlinks and quota violations are rejected. Partial artifacts remain marked partial. Nothing promotes automatically. The reviewed additive promotion API supports one originally absent file, rechecks base/input/output/approval digests and atomically creates without replacing an existing target; existing-file edits and multi-file transactional merges remain unsupported. [isolated-executor.md](docs/build/isolated-executor.md) states the accepted threat model and precise limits, including the trusted host/kernel and absence of a hostile multi-tenant claim.

**Outbound recipients:** strict identities require exact immutable recipient plans; missing/empty plans deny traffic, while identity-absent legacy profiles remain uncertified. Plans distinguish main/aux model, memory, embeddings, MCP, browser/tool, delivery, telemetry, provisioning, training and subprocess purposes; a declaration does not enable an unsupported adapter. Actual HTTP transport guards check destination, path, Host, redirects, live policy and owner before bytes/credentials leave. Real loopback sync/async tests prove a denied redirect destination receives neither headers nor body. A local-only envelope requires literal loopback recipients; LAN Jetson is a separate declared recipient. BE04 cache keys, poisoned-pool handling and per-client cancellation ownership survive the guarded transport wrapper. Native/opaque model routes, startup plugin engines, metadata probes and local-model provisioning cannot bypass the boundary. Strict external gateway/cron/API delivery routes are refused before command/provider dispatch until a recipient-bound BE06 adapter exists; supported local CLI/TUI/Desktop and owned child computation are separate. Offline explicit/static context hints are not live model-capability verification. [recipient-plan-reference.md](docs/build/recipient-plan-reference.md) records configuration and migration.

**MCP:** connection pools, registry overlays and schemas bind agent/policy/configuration/credential/endpoint ownership, not profile alone. Exact operator tool/read grants, actual handler provenance and **mandatory complete tool-schema SHA-256 pins** are required for strict callable tools. Missing/mismatched pins deny first registration and recreated connections, so restart cannot reset schema authorization. Refresh, cached-prefix restoration, reconnect and direct utility reads recheck live scope; server `readOnlyHint` is never authority. Only explicit operator read-only contracts avoid a consequential-action approval. Strict Streamable HTTP uses the recipient boundary; stdio/SSE/OAuth, unversioned prompt/resource refresh and SDK sampling/MRTR remain explicitly unsupported. Budgeted MCP startup/discovery also refuses before config/credential/SDK work because no MCP charge adapter is certified. Primary personal MCP stays unavailable to specialists/children through supported paths. Ephemeral teardown retires only owned pools and performs no unapproved remote DELETE/reconnect; remote cleanup is unconfirmed. [mcp-agent-contracts.md](docs/build/mcp-agent-contracts.md) documents exact configuration and exclusions.

**CUA and RPC corrections:** sanitizer/import failure now prevents privileged CUA launch instead of copying ambient environment; desktop drivers use the non-terminal credential scrubber, so terminal passthrough does not grant credentials. Socket and file RPC preserve token checks and add bounded typed payloads, live policy/owner and tool-scope checks before dispatch; real transports prove a valid token cannot bypass revoked grants or a successor owner. Host CUA is not newly certified by an environment fix.

**Validation:** the final frozen-source 20-file campaign passed **242 tests, 0 failed, 0 skipped**, in 19.8 seconds with five workers and no file retries. Exact commands, per-file counts and log digests are in [be05-validation.json](docs/build/be05-validation.json). Focused evidence includes **31 passing** real executor/workspace tests, **324 passing** egress/provider regressions, **169 passing** final MCP/policy cases, **69 passing** capability/loop cases and the actual startup/CUA/RPC checks. Counts overlap and are not added. Older fixtures that assumed arbitrary handlers or unpinned MCP authorization were migrated to real certified handlers, exact pinned owned MCP, guarded SDK transports and live SQLite scopes; their original isolation, once-only, replay, prefix and failure assertions remain substantive. Generated identity reference/contracts, shared TypeScript, focused Ruff and diff checks pass.

**Environmental failures retained honestly:** broader legacy executor regression: **66 passed, 24 failed** at unchanged `tools/code_kernel.py:574`, where host AF_UNIX creation returns `EPERM`. Standalone Python with no repository imports reproduces the failure; the new namespace adapter uses pipes and passes. Broad MCP regression: **943 passed, 1 skipped, 1 failed** because the existing process-child snapshot cannot see a seeded child; exact HEAD and working function both return the same empty set. Neither limitation was bypassed with host changes or relabeled green. BE00 project-tree failures and consolidated full-project regression remain for their owning gates.

**Rollback and next:** stop/revoke new capabilities and unsafe executors before rollback; retain staged artifacts and hardened denial defaults. Removing policy/grants cannot downgrade an existing session. Live personal-harness contracts, model/server credentials and deployment remain operator inputs, not invented fixtures. BE06 will add durable effects/approval reconciliation and independent delivery recovery; BE07 will expose staged artifacts with their authoritative revision/permission model.

## 2026-10-02 BE06 — Durable effects, exact approvals and recoverable results

**Status:** implemented for the declared local adapters; final validation receipt is recorded below. Source IDs: U15/U16. Base: `c701f27d4e46e7bbb0f1f90f91e1442acadb5015`. Publication is directly to `main` with this journal, without a PR. No live service, credential, deployment, dependency update or training was used.

**Effect authority:** additive SessionDB schema **35** persists immutable intent before dispatch, exact operation/input/target/revision/policy/actor/run bindings, six explicit outcome states and bounded append-only evidence. A dispatched or uncertain effect cannot be automatically replayed. The first certified mutation is real immutable local result publication, using no-overwrite Linux rename and actor-private checked file descriptors. Four actual process-death windows cover prepared intent, dispatched intent, visible bytes and persisted receipt. Read-only reconciliation checks exact current bytes under a deadline and fenced recovery ownership; missing/conflicting bytes stay unresolved. Current-state evidence is distinguished from the original write receipt. Strict consequential MCP calls now fail closed until a semantic durable adapter exists; pinned operator-authorized read contracts remain available.

**Result and delivery:** final/partial result bytes are retained independently of the small command summary. ArtifactVersion visibility, command completion and one delivery obligation commit in one transaction. Published-but-uncommitted bytes can be recovered without pretending execution completed. Owned result retrieval provides digest-checked bounded chunks and delivery discovery; it neither runs an agent nor sends anything. The existing TUI/Desktop prompt-completion path sends an immutable result reference through its owned transport. Successful writes mean transport acceptance only. Explicit token/digest-bound client acknowledgment tracks text and artifact independently. Retry uses the same committed payload/destination without inference or effect replay. Three attempts, persisted jitter/backoff, a 24-hour deadline and 30-day receipt-token retention lead to visible unknown/dead-letter outcomes rather than invented success. Unresolved rows and result bytes are retained; garbage collection awaits later reference/backup policy.

**Approvals and projections:** SQLite replaces process-memory approval authority. Human decisions bind exact actor, action/target/input digests, policy, owner/run, revisions and expiry; repeated, forged, revoked, stale or already-consumed requests are rejected atomically. Missing/withdrawn/timed-out UI decisions remain pending. Owned typed RPCs expose redacted approval/effect recovery and independent delivery controls; generic command approval remains explicitly unsupported. Mission snapshots read bounded references from the authoritative effect, approval and artifact rows in the same transaction; explicit counts/truncation prevent a 100-reference window from implying completeness. Legacy missing model/tool output markers remain distinct unresolved invocations. Generated TypeScript/OpenRPC contracts are updated for later frontend consumers. Cancellation can preserve bounded private partial output without re-enabling model/tool dispatch or accepting a successor owner's finalization.

**Legacy compatibility:** `delivery_obligations` remains the one outbox table, with explicit legacy/runtime authority. Legacy sweeps, profile maintenance and retention cannot mutate runtime obligations. Crash-left and ambiguous legacy sends retain original content/recipient as unknown and cannot auto-resend. Proven never-dispatched reconnect work keeps its safe retry behavior. Legacy rows without an exact turn token conservatively hold automatic resume of that conversation until operator settlement; explicit inbound messages remain usable. Lifecycle records remain forensic evidence. Schema-compatible readers, consumption/idempotency history and unresolved bytes must survive rollback.

**Limits and consumer boundary:** local Linux immutable publication (8 MiB maximum) and owned local JSON-RPC notification are the certified adapters. No remote exactly-once mutation, external send receipt, human-read confirmation, project output promotion or live harness certification is claimed. Existing external gateway/cron strict-runtime denial remains. Full frontend delivery/approval presentation belongs to the FrontEnd plan. See [durable-effects-delivery.md](docs/build/durable-effects-delivery.md) for APIs, truth states, retention and rollback.

**Validation:** the consolidated 21-file campaign passed **301 tests, 0 failed**, in 25.4 seconds with five workers and no file retries. After the final malformed-event guard and legacy-hold fixture assertions, the settled-source four-file subset passed **73 tests, 0 failed**, in 10.3 seconds. Counts overlap and are not added. Real SQLite reopen/concurrency/rollback, four process-crash windows, full-result reconstruction, actual owned transport/RPC, partial acknowledgments, cancellation, successor fencing and legacy recovery are covered. Exact commands, per-file counts and log hashes are in [be06-validation.json](docs/build/be06-validation.json). Changed Python Ruff, generated contract freshness, shared TypeScript and staged/unstaged diff checks pass. Full-project regression remains BE18; inherited project-tree, AF_UNIX and process-visibility environmental failures remain recorded, not relabeled green.

## 2026-10-02 BE07 — Project grants, immutable revisions and traceable source context

**Status:** implemented for the declared Markdown/local-control adapter; validation receipt follows below. Sources: F01/F03/F04/F10/F11/F32 and U20. Base: `a138372d8df64a5424a64df8e4ea73c41a2cd290`. Publication remains direct to `main`, with no PR, live service, credential configuration, dependency update, deployment or training.

**Project authority:** existing per-profile projects retain IDs/slugs/folders while gaining owner, revision, purpose, selected source/artifact/mission references and exact principal/agent grants. Strict access intersects immutable policy IDs with live grants; cross-agent sharing is explicit and revoked reads fail immediately. Owned user controls can create/adopt legacy ownerless records and CAS metadata/grants without granting model access or editing immutable policy. The control cannot be minted from a model run or inherited transport context. Legacy broad strict project discovery/mutation routes and cwd-only project inference fail closed; identity-absent legacy behavior remains. Existing folder/project writes advance revision and invalidate stale updates.

**Single artifact catalog:** additive schema **36** preserves the BE06 result catalog and blob bytes, replacing only the broad command-uniqueness constraint with a partial runtime-result index. Immutable project-version reservations, metadata, lineage and head CAS share SessionDB authority. `artifact_heads` alone owns same-artifact current version; projects.db selected references are reversible filing metadata, explicitly not a second head. Publishing followed by project filing is not advertised as cross-database atomicity. Stale head edits retain branches; declared derivatives become stale when their source changes.

**Complete Markdown workflow:** owned typed prepare/publish/get/status/cancel controls execute a dedicated bounded artifact command without a provider client or ordinary model/tool authority. Exact content/target/revision/policy approval is durable and one-use. Original 300-second lease/deadline and budget root survive replay; expired/successor/cancelled ownership refuses new mutation. A distinct project-publication effect adapter never uses the BE06 finalization exemption. Checked immutable bytes retain complete Unicode; bounded downloads verify full digest/size and require plain-text preview. Targeted heading edits preserve untouched/locked sections exactly; selected-delta three-way merge refuses conflicting content. Confirmed publication before catalog commit remains recoverable without claiming a committed version or moving a head.

**Sources and resume:** captures preserve original immutable references/bytes, distinct acquisition metadata, annotations, extraction history and reversible filing. Extraction failure retains the source, and URL metadata never triggers an implicit fetch. Deduplication only suggests review. Templates retain explicit structure/style/assets/slots/exclusions and immutable baseline lineage. Typed evidence anchors record spans/IDs/decisions/constraints/approvals/artifact versions with authority/freshness; labels never grant permission or establish truth. Bounded resume context joins authorized project references, current artifact versions, safe same-actor mission state and explicit stale/missing blockers, without personal-memory or transcript access.

**Scope and rollback:** UTF-8 Markdown is the first complete adapter, with an 8 MiB service bound and 65,536-character upload control; arbitrary binary extraction, active-content rendering, remote sharing and full frontend editing UX remain separate gates. Store migrations are preserving and interrupted rebuilds roll back. Rollback must retain schema36-aware readers and the runtime-result partial index; schema35 binaries cannot safely read multiple project versions per command. Preserve originals, immutable history, consumed approvals and unresolved effects; no automatic blob/source garbage collection. The two inherited project-tree tests that discover an extra `tmp` group remain documented baseline/environmental failures, not a reason to alter the scanner. [project-artifact-contracts.md](docs/build/project-artifact-contracts.md) documents APIs, authorization, transaction boundaries and recovery; frontend artifact/source consumers remain in their own plan.

**Validation:** final frozen-source 23-file campaign passed **304 tests, 0 failed**, in 27.3 seconds with five workers and no file retries. Real approved Markdown publication, complete Unicode download, actual owned RPC, cross-profile/grant revocation, exact approval, targeted edit/branch/merge, three real project process-crash windows, catalog recovery, preserving migration rollback, derivative invalidation, source/extraction preservation and evidence/resume boundaries are exercised. Exact commands, counts and log hashes are in [be07-validation.json](docs/build/be07-validation.json). Additional focused storage/project/service campaigns passed and overlap the consolidated count. Changed Python Ruff, generated contract freshness, shared TypeScript and diff checks pass. Earlier intermediate control-budget failures were corrected and rechecked; no unresolved new test failure remains. Full-project regression and the documented inherited environment failures remain for their owning gates.

## 2026-10-02 BE08 — Isolated memory routing and transactional context recovery

**Status:** local backend implementation completed for the declared adapters; live personal-harness qualification remains explicitly pending the operator's exact deployed schemas and secure configuration. This entry does not claim live P02/P03 harness acceptance. Sources: corrected U19–U24 and F05/F06. Base: `580a17f10def764e228944434a1c5b4283e29abc`. Publication is direct to `main`; no PR, live credential use, endpoint test, dependency update, training, remote data export or deployment.

**Single owner/backend:** strict primary identity selects only the personal MCP provider; specialists and ephemeral children select isolated built-in stores. No name-based primary switch, hidden primary fallback, provider fan-out, shared child store or private write queue is introduced. Constructor-bound namespace checks survive A→B→A profile switches and child resume. Strict review forks/shared pending-review surfaces remain disabled where their old implementation shares ownership. Provider-manager reuse, teardown and cache keys bind authenticated identity/home/configuration/privacy, endpoint and credential scope; disabled providers receive zero lifecycle payload.

**Structured built-in authority:** each individual namespace has one SQLite catalog with versioned typed records, exact owner/source/author/time/validity/confidence/scope, CAS conflicts, supersession and tombstones. MEMORY.md/USER.md are bounded validated derived projections; real process-crash recovery restores them from the catalog, while external drift/symlinks refuse. Namespace retention is explicit and closing a child does not erase supported resume state. Record/history/conflict/disk caps prevent unbounded growth. Procedure references require a positive canonical workflow version and remain uncertain pending BE11 authority. Local deletion/export semantics do not promise physical erasure of backups or earlier copies.

**Real routing and controls:** delegated child construction now deliberately enables only its own granted memory tool. Existing lexical/session search filters owner/home/lifecycle before SQL limits/snippets and before direct-ID/title/browse/scroll/lineage reads; primary personal memory cannot silently become local session recall. Owned typed memory RPCs cover status, records, stable-ID CAS/tombstone, revision-bound full-digest local export and explicit granted-project applicability. Project notes are excluded from the global prefix/default recall. Independent bounded scope cursors and applicability notices prevent project switches from silently carrying old context forward.

**Personal harness boundary:** the actual generic MCP adapter uses the existing certified bearer/egress/schema/owner/broker path. Operator configuration supplies exact tool schema pins, argument names, response schema/mapping and bounded context packages; no deployment token or endpoint is embedded. Complete context packs preserve relevance/usage/provenance as untrusted data, never permission. Config-ready health says `live_unverified` until validated recall. Partial/error/malformed/oversize/timeout results yield degraded active-context/project-only operation. Supplied descriptive documentation did not establish complete mutation/supersession/delete/export/recovery guarantees, so those operations remain unsupported rather than fabricated. Strict budgeted MCP remains gated on a certified charge adapter. No duplicate primary semantic index, auto-ingestion or unbounded private retry queue is built.

**Fresh context and compaction:** corrections/invalidation enter one bounded persisted current-turn API sidecar; the exact cursor is acknowledged only after matching stored bytes. Ordinary historical messages and the frozen system prefix stay unchanged. Additive SessionDB schema **37** commits ContextProjection/checkpoint references, source archives, replacement transcript, concurrent tail, native provider sidecars and the sanctioned compressed prefix in the same writer transaction. Generation/revision/source-byte fences reject stale/torn changes, and protected evidence/approval overflow retains the original source instead of silently dropping anchors. Strict rotation is refused before summary because only atomic in-place compression is certified; legacy rotation remains unchanged.

**Qualification and rollback:** Linux checked individual-store operations and local synthetic transport/SDK fixtures are the current verification scope. Conditional hybrid indexing and provider federation remain disabled without measured need and separate approval. Preserve namespace/history/tombstones, compatible projection/checkpoint readers, source archives and exact grants; never downgrade by copying personal-harness data into a shared store. [agent-memory-contracts.md](docs/build/agent-memory-contracts.md) and [memory-backend-contract.md](docs/build/memory-backend-contract.md) document capabilities, limits and credential-reference-only configuration. Frontend memory controls and operator live acceptance remain separate gates.

**Validation:** consolidated 29-file campaign passed **470 tests, 0 failed**, in 33.4 seconds with five workers and no file retries. The final disabled-target correction was then rechecked across five settled-source files: **99 tests, 0 failed**, in 11.7 seconds. Disabled targets now withhold current/history/conflict content, recall/export/fresh deltas and prefix/property reads while retaining records for re-enable. Counts overlap and are not added. Actual admitted child construction/resume and enabled-budget memory/search dispatch, same-profile and cross-profile isolation, SQLite/process-crash recovery, scope switching, exact RPC exports, synthetic bearer/registry/broker checks, prefix/cache privacy and atomic compression/micro-failure rollback are covered. Exact commands and hashes are in [be08-validation.json](docs/build/be08-validation.json). Ruff, generated contract freshness, shared TypeScript and diff checks pass; `.env` ignore status is verified. Full-project regression and inherited environment failures remain for BE18. These local results do not substitute for live personal-harness qualification.

## 2026-10-02 BE09 — Bounded missions and evidence-backed completion

**Status:** implemented and locally verified for declared artifact/test adapters; empirical human-review comparison remains an operator study, not a synthetic-test claim. Sources: F02/F09/F10/F26 and U26. Base: `7861692f1f4a2b6fd591a25684c941af2c37853f`. Direct `main` publication with no PR, live service, credential configuration, dependency install, deployment or training.

**Mission authority:** additive schema **38** extends the existing goal/continuation model with a root-owned, revisioned mission, ten explicit states, typed deliverables/criteria/dependencies/plan steps, project scope, original budget/deadline, immutable verification and bounded audit history. Legacy goal rows are migration input/audit or compatibility projection, never competing authority; old `done` does not become verified completion. Conservative reviewed mode is default, direct mode requires explicit low-risk/low-uncertainty declarations, and consequential/high-uncertainty work needs plan/checkpoint information. Finite DAG validation and early limits reject incoherent/oversized intent before writes. Goal revision/resume/set/clear never resets consumed budget or extends the original root ceiling.

**Actual verification:** committed BE07 bytes and live grants drive existence, section, exact-text, bounded local JSON-schema and linked-output consistency receipts. Each receipt binds criterion/version/digest and dependency fingerprints; writer-time revalidation rejects stale proof while preserving unaffected accepted outputs and explicitly pinned versions. A model `DONE`, source claim, successful response or untrusted exit-code string cannot certify a criterion. Two linked outputs are exercised through mismatch, repair and correct readiness/completion.

**Certified test execution:** a narrow `isolated_python_v1` adapter actually executes declared code against exact immutable artifact inputs using the existing BE05 Linux isolation and mandatory BE03 budget/grants. It journals intent and persists a host-observed one-use receipt with exit status and raw captured-byte digests. Limits are four tests/run, 8 KiB code and five seconds/test. Network/shell/package/uncertified-executor paths are unsupported; fake success printed to stdout, incomplete output, changed inputs and unknown termination do not pass. Unknown termination retains its slot. Idle verification only reads existing receipts; it never fabricates an execution owner.

**Existing runtime integration:** verification and bounded finalization run before command completion while the real RuntimeRun is bound. Existing CLI/TUI continuation consumers claim the stored decision once instead of starting a second loop or strict post-return judge. No-progress/guardrail evidence and recovery ceilings persist across turns. Model-run project publication rechecks mission project/admission revision as an additional constraint, never as a grant. Budget/cancellation returns usable artifacts and exact unresolved work, with no effect-undo claim. Only an explicit `personal_memory` dependency consults current owned memory health, without fetching; unavailable/nonprimary/unverified sources wait honestly.

**Controls and races:** owned typed mission APIs and the owned TUI shared `/goal` parser use exact human-control witnesses, finite leases and CAS. Human acceptance, deterministic validation, execution and delivery remain separate. Plan/input/target changes selectively invalidate affected pending or approved-but-unconsumed approvals with explicit invalidation reason; consumed history remains. Tests cover steer before acceptance, after acceptance and between verification/result commit, plus cancellation during verification. Late instructions become missed/not-applied with effect references, never retroactive edits or stale continuations. BE06 delivery retry remains independent of execution.

**Policy measurement and limits:** the local paired-observation CLI compares identical task/acceptance fingerprints, cost basis, latency and active user-review time. Missing time is not zero; synthetic/reported/failed observations cannot establish measured review savings. No automatic policy promotion or empirical/statistical claim is made. [mission-contracts.md](docs/build/mission-contracts.md) documents supported controls/adapters and rollback. Live personal-memory qualification remains BE08's explicit gate. Full frontend presentation, arbitrary host test commands and additional production formats remain in their owning phases.

**Inherited environment evidence:** an optional legacy verification-command suite showed 18 failures because the environment's read-only `/tmp/.git` directory is not a Git repository but the unchanged ancestor detector selects it. A standalone probe reproduced empty discovered verification commands, and exact BE08 source hashes match for both `coding_context.py` and `verification_evidence.py`. No host directory change, dependency install or unrelated scanner refactor was used. This is recorded separately from the certified adapter and consolidated checks, not called green.

**Validation:** consolidated 27-file campaign passed **465 tests** and exposed one queued-cancellation regression. The narrow fix preserves atomic queue-only cancellation while rejecting a target claimed/completed during launch. Settled-source canonical checks across three affected files passed **40 tests, 0 failed**, including both new launch-race cases and the original queue-cancellation case. Counts overlap and are not added. Exact commands and log hashes are in [be09-validation.json](docs/build/be09-validation.json). Changed Python Ruff, generated contract freshness, shared TypeScript and diff checks pass. No unresolved new failure remains in this focused scope; full-project consolidation remains BE18.

## 2026-10-02 BE10 — Source-backed domain production and validated file bundles

**Status:** local backend implementation checkpoint; selected local production demonstrations are verified, while live connected-source P08, broader browser/repository execution and measured human cleanup savings remain explicit pending acceptance gates. Sources: F07/F08/F09/F12/F13/F14/F15/F25/F26/F28. Base: `5c3e9c12f50e1965597e55b192da518fc09a5043`. Direct `main`, no PR. No live credential, connector deployment, paid provider, dependency update, model download, training or security-setting change.

**Reusable production path:** typed bounded DomainJob dispatch calls finite installed adapters under owned project authority, using the existing artifact command, original lease/budget, exact approvals, effects, immutable catalog and head CAS. Complete outputs and an approved JSON manifest retain source digests, transformation recipes, validators and lineage. Bundles publish members independently and their manifest last; partial publication is not described as atomic or complete. Typed bytes-import and domain prepare/publish controls are real backend consumers, with existing status/cancel/download/recovery and live grant checks. They add no core model tool, provider loop or schedule owner. CLI request validation is explicitly syntax-only.

**Actual formats:** complete-byte allowlisted validation covers Markdown/text/CSV/JSON/notebook/restricted XLSX/PCM WAV/restricted PNG/SRT/VTT at prepare, broker dispatch, publish and reopen. Exact legacy Markdown approvals remain compatible. Binary preview is download-only, not active content. Structure, digest, dimensions, sample/duration and subtitle checks do not imply visual/audio/semantic fidelity. Unsupported Office features/macros/external links, unsafe ZIP/XML and arbitrary MIME fail closed. DOCX/PPTX/PDF conversion and broad rendering remain unsupported rather than renamed files or successful-exit claims.

**Research and living briefs:** exact original artifact/capture resolvers bind authenticated scope, immutable version/digest, byte evidence, authority, freshness/coverage and missing-source health; they are two local resolver types, not two live SaaS connectors. Captures resolve originals, never annotations masquerading as source. Manual refresh changes only affected factual/interpretive claim spans, preserving user edits, locked sections and prior versions. Owned research/brief controls revalidate sources and publish the updated brief then a separately approved immutable JSON dependency sidecar. Pending recommendations retain exact prior evidence pins; later refresh reads the sidecar. Partial publication and omitted volatile retrieval timestamps are explicit. Citation membership does not establish semantic truth.

**Decision and data:** exact rational decision calculations separate facts-as-declarations, assumptions, subjective scores, priorities, weights/ranges and accepted choice; sensitivity recomputes without authorizing actions. Bounded CSV/XLSX ingestion requires explicit ambiguity/null/unit/currency/date/duplicate policies and manual mapping. Declared joins and aggregations retain original source bytes, recipes and each output row's lineage. Complete CSV/workbook/notebook exports and real bar/line PNGs are independently reopened. Formula text/caches remain distinct; no native recalculation or cached-value arithmetic claim. CSV formula-leading text/header hazards refuse, while typed numeric negatives remain valid. The actual owned API demonstrates source upload → aggregate → approved workbook/notebook/chart publication → complete download and trace.

**Teaching, meetings and creative work:** versioned curated rational exercises have verified answers, hints, bounded adaptation, visible immutable history, reset, chosen revisits and separate transfer assessment. Tutor resume validates replay and profile ownership; educator updates preserve instructor edits and locks while checking objective/activity/exercise/key coverage. Supplied transcript ingestion retains timestamps, gaps, speaker uncertainty and original bytes with explicit consent declarations/retention scope. Cited commitments remain proposals. Creative packages retain rights/reference/continuity metadata and validate supplied actual assets; prompt-only stages say external production remains pending and never invent generated media.

**Coding/browser scope:** a real fixture validates exact baseline/candidate Python artifacts through the certified BE09 isolated test adapter, then publishes/reopens changed-file review outputs and authenticated test evidence. Repository/worktree/baseline declarations are not host Git attestations; release, merge, deployment and unsupported promotion remain separate gates. Browser diagnostics bind current page/input/target/age and authoritative effect state, retain unknown-after-submit, and refuse to certify generic payloads or an unconfigured browser action. No real external browser submission or live generation was performed.

**Rollback and acceptance limits:** no database migration beyond schema38. Adapters can be disabled independently while retaining original inputs, artifacts, sidecars, approvals, partial effects and compatible readers. Do not replay mutations to reconstruct outputs. Local fixtures establish supported behavior, not learning effectiveness, semantic fact-checking, live P08 or measured cleanup savings. [domain-production-contracts.md](docs/build/domain-production-contracts.md) details interfaces, limits and conditional gates. Full frontend presentation remains separate; BE11 can build on these scoped deterministic adapters without silently enabling the pending gates.

**Validation:** final frozen-source canonical campaign passed **415 tests, 0 failed across 25 files**, in 32.8 seconds with five workers and no file retries. It exercises actual owned source/brief/domain/bytes RPCs, exact approvals, complete XLSX/notebook/PNG outputs, source-row traces, versioned tutor resume, real isolated coding tests, publication/reopen, live revocation, A→B→A isolation and partial-bundle retry. Additional lane counts overlap and are not added. Exact commands/per-file counts/log hashes are in [be10-validation.json](docs/build/be10-validation.json). Changed Python Ruff, generated contracts, shared TypeScript and diff checks pass. Earlier unbound-helper RPC integration failures were fixed and are green in this campaign. No unresolved new focused failure remains; the listed live/empirical gates are not claimed passed.

## 2026-10-03 BE11 — Version workflows, templates and evaluated procedural reuse

**Checkpoint:** direct-main publication pending behind the existing BE10 upload approval; local implementation checkpoint builds on `1228b3cefb27dabe67a32bfc69f376ff86c0c6a1`. No PR.

Implemented immutable bounded WorkflowVersion/TemplateVersion contracts, canonical content digests, additive schema38→39 registry, provenance verification, deterministic local evaluation, exact human lifecycle/sharing decisions, and manual Mission-bound workflow execution. Added owned `runtime.workflow.*` APIs and generated shared TypeScript/OpenRPC contracts; the CLI validates definitions without minting execution or approval authority.

Before: successful work had no canonical evaluated workflow lifecycle or immutable resume binding. After: a draft can be evaluated on varied tuning/held-out inputs against retained generalist outputs and its exact predecessor, promoted through an exact owned human approval, and rerun on a second input without rewriting instructions. Immutable templates remain separate style metadata. Corrections/failures retain artifact evidence without implicit prompt/weight training. Source-derived procedures stay private and export requires an exact recipient/content grant.

Authority/recovery: runtime uses existing live project ceilings, Mission revisions, admitted budget accounting, artifact approvals/effects, session fencing and control cancellation. Model-runtime self-promotion fails even through a genuine owned transport. Rollback only moves the active pointer to an approved predecessor. Revocation stops new/prepared publication, preserves history/effects and is rechecked under the live project guard for export. Workflow pins retain content/version, original input, environment/template, Mission and budget root. Expired controls fail closed pending inspection/reconciliation, never adopt a changed recipe.

Validation receipts: eight-suite focused runner passed 86 tests / 0 failures in 11.2s; after fixing the concrete concurrent-export revocation race, final affected RPC+authority rerun passed 11 tests / 0 failures in 11.7s (nine RPC and two authority tests). The unique final selected suite set contains 87 cases. Coverage includes schema38 legacy session/Mission/dispatched-effect/consumed-approval preservation with A→B→A reopen, project/profile isolation, varied reruns, held-out split preservation, immutable edits, exact promotion, rollback, wrong-recipient export denial, demonstration consent, templates, correction evidence, finite domain reuse, revocation between prepare/publish, parameter pins, cancellation and literal byte-bounded interpolation. Ruff passed all BE11 Python files/tests. Generated-contract freshness passed. Parent ran `npm run typecheck --workspace @hermes/shared` successfully; generated shapes unchanged afterward. BE11 tracked-path `git diff --check` passed. Commands and limits: `docs/build/be11-validation.json`; interface: `docs/build/versioned-workflow-contracts.md`.

Scope/remaining gates: finite local Markdown and installed BE10 producers only; no live host shell/browser/account execution, arbitrary imports, model inference, actual external sharing, automatic personal-harness discovery, training, or measured user-cleanup savings. Generalist outputs and exact expected digests are explicitly human supplied rather than independently attested. Scheduling and paused scheduled occurrence integration belong to BE12, which can use the exact approved-version `resolve_executable` consumer. FE03/FE06/FE07 can consume generated APIs; no frontend behavior added. Full repository suite not run. No dependency installs, live secrets, hardware provisioning, Git staging/commit or publication performed by this worker.

## 2026-10-03 BE12 — Durable local schedules, monitors and accepted commitments

Base: verified remote BE11 `52560ba9fd12ee0dc553e62f305583a96e6762b0` (same tree as locally validated BE11 checkpoint).

Status: implemented and locally verified for the explicitly finite local adapter boundary. Live inbox/calendar/HTTP/agent/script/event/send adapters, semantic extraction and autonomous memory/skill promotion are not certified or enabled. Parent owns commit and publication. No live user configuration, credentials, scripts, providers or external actions were used.

Before: inherited cron already had tick locks, execution claims, pending-slot recovery, process fencing and unknown-send safeguards, but strict pre-agent prompt preparation could run scripts/URL sources before owner binding. There was no immutable SQLite schedule/version/monitor/commitment authority or owned finite consumer.

After:
- Schema40 appends imported schedule and commitment table SQL to canonical SessionDB migration; no second writer/database or tick loop
- Existing scheduler.tick, under its existing per-profile lock and drain/ESTOP gate, scans finite owner-bound SQLite schedules
- Runtime submit admission callback atomically commits occurrence ID, immutable version, runtime command/run linkage, original deadline, next-due advance and check-budget debit
- Stable persisted owner resolution precedes all reads and ignores current active-agent default; home/principal/policy changes deny. Strict legacy run_job gates before monitor/script/provider/MCP activity, even no_agent/prerun paths. Legacy skip_memory=False source assumption corrected
- UTC occurrence digest, explicit IANA clock/fold/gap rules, skip/latest missed policy, bounded scan, overlap block and dedup tombstones. Unclaimed accepted work can recover; claimed unknown work never replays. Pause/revoke cancels unclaimed admissions and preserves claims/unknown effects
- Exact local artifact monitors with normalized/structured/threshold predicates, silent baseline, cosmetic suppression, unhealthy source failures and atomic observation/baseline/notification-action intents
- Conditional observation is distinct from human local-review grant; exact target/input/schedule digest, expiry, freshness, bounded fires revalidated and consumed at trigger. No standing external communication authority
- Immutable bounded memory/skill review records use the BE11 resolve_executable version/digest/template/revocation seam and produce completion receipts, never inherit primary memory, ingest memory or selfpromote a skill
- Imported inbox snapshot candidates, explicit accepted obligation authority/history, terminal-safe waiting/weekly review, exact correspondence draft-vs-confirmed-effect proof, and availability-only timezone-aware calendar preview with current participants/attachments/date uncertainty retained
- Owned typed RPC handlers registered and Python-generated TypeScript/OpenRPC refreshed, including exact human-only schedule, inbox, acceptance, correspondence controls
- Paused deterministic Dots import rejects active/unknown declaration, retains declaration+command/run, explicitly does not certify foreign retirement. No external cutover occurred
- Delivery matrix documents local intent versus legacy cron and BE06 gateway ambiguity. No blanket exactly-once or at-least-once assertion and no delivery failure reruns completed execution

Migration / rollback:
Additive schema40, no auto-import or job activation. New records are paused. Before ownership or binary rollback, pause, drain/reconcile old accepted/claimed/unknown occurrences and retain tombstones. Foreign Runner retirement requires operator verification; imported snapshot says foreign_cutover_verified:false. Existing strict agent/script/URL paths remain fail-closed pending certified adapters.

Validation receipts (2026-10-03 UTC):
- Initial unprefixed runner attempt: tests NOT RUN; stale activation attempted /home/agent/.hermes and failed read-only. Corrected with existing verified .venv interpreter; no install performed
- Canonical runner prefix: HERMES_HOME=/workspace/shared/ryoko-dev-home HERMES_RUNTIME_DIR=/workspace/shared/ryoko-runtime HERMES_PYTHON="$PWD/.venv/bin/python" scripts/run_tests.sh
- Four early focused cron/identity files: 9 passed
- Commitment worker regression: 45 passed across test_commitments.py, test_commitments_rpc.py, test_artifact_commands.py and test_artifact_rpc.py; final affected commitment rerun12 passed. Details /tmp/be12-commitment-journal.md
- Initial owned schedule E2E found an incorrect internal fence argument; corrected. Next run exposed test raw microsecond anchor versus canonical millisecond due; corrected test to use returned authoritative next_due. Subsequent owned E2E6 passed
- Main focused/regression gate: 16 files, 257 passed, 0 failed, no retries,18.7 seconds. Exact log /tmp/be12-validation.log. Includes actual scheduler/occurrence/execution/delivery/monitor regression, owner construction, commitments and generated-contract freshness
- Final-code gate after bounded status/truncation metadata and schedule-scoped RPC helper naming: 3 files, 10 passed, 0 failed, no retries. Includes atomic-intent rollback, actual schema39→40 reopen and generated-contract freshness; exact settled log /tmp/be12-final-focused.log
- Ruff over all new/changed topical Python and tests: passed
- git diff --check: passed
- Generated contracts regenerated through .venv/bin/python scripts/gen_gateway_contracts.py; generated freshness included above
- Full repository suite: NOT RUN. Live provider/mailbox/calendar/browser/Dots cutover/remote execution and live sends: NOT RUN; no certification claimed

Evidence:
Real owned RPC dispatch and immutable artifact bytes feed actual scheduler.tick and reopened SessionDB. Tests assert silent baseline, cosmetic suppression, meaningful changes, missing bytes unhealthy, false-to-true predicate, separate finite grant, original immutable inputs, approved workflow revoke, atomic command admission rollback, restart of accepted work, death after runtime claim but before occurrence stamp, never replaying unknown, cancel-before-claim, expiry, version/import controls, DST fold/gap and A→B→A identity scope. Commitment tests verify false urgency suppression, unresolved source dates, one accepted obligation, evidence CAS, terminal/supersession preservation, revoked ACL, draft promise flags, exact sent-effect proof and supplied availability honesty.

Consumer cross-links:
Backend owned RPC + existing cron consumer ship in this phase. No FE consumer/renderer implementation is included. BE11 canonical resolve_executable is reused. BE13 and BE14 working files are excluded; exact BE12 staging whitelist is /tmp/be12-files.txt. Do not stage hermes_state_runtime.py or hermes_state_delivery.py (BE14), or specialist/delegation files (BE13).

Final settled receipt manifest: `docs/build/be12-validation.json` contains exact canonical commands/counts plus SHA256s of main/final logs and parent shared TypeScript log. Parent shared check `npm run typecheck --workspace @hermes/shared` passed with unchanged generated contracts. All BE12 implementation files are frozen for parent checkpoint; no staging or commit performed by this worker.

## 2026-10-03 BE13 — Durable scoped specialists and finite local service/media boundaries

Status: bounded local implementation validated; full measured-benefit/live capability exit gates remain pending. No staging, commit, remote publication, deployment, private-data ingestion or external service activation was performed by this worker.

Before: the strict certified delegate tool could reach the existing process-local child construction and daemon runner without a BE13 immutable handoff, live executor placement record or durable root fan-out admission. Async dispatch/completion/recovery already persisted useful records, but durable completion recovery was not process resumption. Stable individual memory existed in BE08; named specialist responsibility/method/output/delegation binding was missing.

After: strict delegation requires explicitly enabled finite local durable configuration and the existing inherited run budget. Stable configured specialist construction is a trusted scope, with exact artifact-backed methods, output schema and its own persistent built-in namespace; ephemeral children retain separate namespaces. Constructor-bound policy intersection, personal MCP exclusion, exact source/mission/child ACLs and a live local executor ticket reach the actual existing batch/async consumers. Root total fan-out/concurrency/depth and one-shot launch are transactional. Isolated code gets only accepted child-staged input files; no ambient parent cwd/read cache/shared terminal alias. Child completion remains separate from parent claim/ack. Missing/wrong parent identities cannot consume strict completions; direct strict async calls cannot fall back to an untyped legacy runner. Parent-loss recovery classifies orphaned/unknown work and never respawns or reports resumed execution.

Schema 40→41 adds canonical delegation root/handoff records, bounded local service pipeline/stage receipts and verified channel mappings. Two real finite local document adapters disclose bytes/location before explicit exact-digest execution, retain completed stages through outage/reopen, and keep staged output separate from artifact publication approval. Eighteen typed owned RPCs wire services, explicit push-to-talk speech interfaces, selected-frame inspection/annotation/confirmed text workflow and local/voice/screen same-mission input deduplication to existing runtime commands/queues. Shared client contracts regenerated and typechecked.

Validation receipts (exact hashes and final source manifest: docs/build/be13-validation.json):
- Canonical combined19-file campaign /tmp/be13-validation.log: 243 passed,9 failed,1 native-macOS skip,20.9s. Preserved unchanged as a failure receipt
- Failures were eight old claim test doubles rejecting new owner_context kwargs and one source-denial assertion expecting a different exception class. No production authority fallback was added
- Final affected4-file rerun /tmp/be13-final-affected.log:26 passed,0 failed,3.7s; correct cross-profile ArtifactStoreError identity_mismatch asserted
- Final strict direct-async regression + existing async suite /tmp/be13-async-final.log:49 passed,0 failed,1 native-macOS skip,20.1s
- Earlier affected delegate/schema/constructor campaign:150 passed; media/runtime/artifact/mission adjacent regression:44 passed, with final changed media paths included in canonical campaign
- Ruff across BE13 Python files, git diff --check, generated-contract freshness and parent-run npm shared TypeScript all passed
- Actual temporary-home SessionDB migration preserves prior messages; real registered service/media RPC paths exercise artifact/ACL/mission/queue behavior. Tests use synthetic sources and finite speech doubles, never live speech/hardware qualification

Limits/rollback: parallel strict teams are blocked with delegation_team_unqualified until measured benefit is demonstrated; no fabricated team benchmark, live voice/STT/TTS, arbitrary screen action, remote service/executor migration, Slack identity adapter or running-process resumption is claimed. Only local executor tickets are supported, with actual isolated-Python probes required when granted. Native macOS remains a separate lane. Disable new strict delegation admission, stop/drain via existing exact-owner controls, and preserve completion/budget/effect ledgers and original memory namespaces. BE14 informed of retained private delegation-workspaces and bounded-service BLOB/receipt inventory. Do not remove schema41 state to fake downgrade compatibility.

Interface consumer follow-up: FE10 specialists should project recorded handoff/completion/delivery state; later F16/F17/F27 clients should consume advertised unsupported capabilities and explicit processing location. Current FE builds have typed contracts but no claim of completed frontend UX or live capability qualification.

Exact changed files: /tmp/be13-files.txt (34 paths). Implementation docs: docs/build/delegation-contracts.md and docs/build/media-service-contracts.md. Parent owns the root buildjournal append, direct-main checkpoint, push and CI verification.

## 2026-10-03 BE14 — Scoped operator controls, privacy manifests and offline qualification

**Status:** backend operator slice implemented; BE14 acceptance remains partial.
Sensitive-ingestion, deployment and full-recovery certification are explicitly
false, not inferred from passing synthetic fixtures.

**Before → after:** Operators previously had separate raw database/effect/outbox
utilities but no common bounded, identity-bound preview/apply consumer. New
`python -m hermes_cli.operations_cli` supports read-only redacted inspect/audit,
retention inventory, exact authorized repair/deletion previews and applies.
Repairs reuse existing SessionDB writer, turn lease, effect reconciler, outbox and
FTS admission. A separate explicit digest is required after reviewing the saved
preview. Wrong-profile, stale revision, target drift, policy revocation and
partial strict fences fail closed. There is no mark-success endpoint.

Supported repairs are read-only effect reconciliation, same-budget outbox
requeue without sending, exact-generation lease revocation without claiming
remote cancellation, and small single-actor FTS rebuild. Mandatory journal
failure blocks the adapter, and revoke/deletion audit writes are transactional.
The optional analytics sink is opt-in, allowlisted/HMAC-pseudonymized, bounded
drop-newest and nonblocking even under a hung sink.

**Privacy:** Transcript manifests actually erase selected message rows/sidecars,
FTS projections, title/system-prompt reference and unreferenced system prompts.
Individual-memory manifests remove content/source/author payloads from every
logical record version and conflicting proposal, synchronize Markdown and retain
structural tombstones. Preview is read-only. Accepted/claimed/unresolved work and
nonterminal missions block erasure; competing leases and source CAS protect
apply. Memory catalog and audit are separate existing stores: a crash between
them remains a requested manifest without a false closure acknowledgment.
Recovery journals, context projections, mission evidence, artifacts, activity
metadata, session JSON/JSONL/request dumps, exports, backups, external caches,
provider copies and frozen process copies are explicitly retained/unverified.
Logical deletion is not forensic erasure; all receipts say complete_deletion=false.

**Backup/provisioning:** Authenticated AES-256-GCM archive envelopes use the
already installed cryptography implementation and caller-supplied ephemeral
fixture keys; no key provisioning or persistent credential was performed.
Synthetic real SessionDB snapshots reuse the updater's WAL-safe copier and
restore only into disposable directories, proving integrity/foreign keys,
expected schema/session scope, key-loss failure, tamper rejection and rotation.
This does not encrypt a live store or alter updater snapshots. Offline extension
inventory uses PM declaration parsing, pins actual source/lock/environment bytes
and exact grants, refuses changed/revoked metadata and executes no extension code,
network probe or dependency install. PM remains the provisioning authority.

**Lifecycle completion:** Explicit pin approval now publishes a private sealed
artifact copy and trusted per-profile registry. The actual general-plugin loader
checks source/artifact digests, exact existing grants, revocation and interpreter/
platform before native/portable activation; imports use the sealed copy, so a
post-validation source swap cannot change executed code. Targeted revoke uses
the existing manager unload without removing unrelated registrations. Opted-in
category/memory/model/entrypoint bypasses fail closed before imports. Absent pins
preserve existing behavior. No real plugin activation/install was performed.

**Migration/flags:** No new database, table, schema bump, environment variable,
model tool, background retention daemon or telemetry default. The new
plugins.pinned_manifests config key defaults to an empty opt-in registry. Four
closed operations event kinds extend the existing mandatory runtime journal.
`repair_runtime_delivery` accepts a complete optional lease/state/attempt fence
for strict operator calls while preserving existing caller behavior.

**Changed files owned by BE14:**
- agent/operations_audit.py
- agent/operations_control.py
- agent/operations_privacy.py
- hermes_cli/operations_backup.py
- hermes_cli/operations_cli.py
- hermes_cli/operations_extensions.py
- hermes_cli/operations_extension_lifecycle.py
- hermes_cli/config_defaults.py (empty opt-in pinned_manifests default)
- hermes_cli/plugins_loader.py (pre-import pin gate)
- plugins/plugin_loader.py (unsupported pinned category gates)
- plugins/memory/__init__.py (pinned entrypoint/warm-up gates)
- providers/__init__.py (pinned model-provider/entrypoint gates)
- hermes_state_runtime.py (four event kinds only)
- hermes_state_delivery.py (strict repair transaction fence only)
- tests/agent/test_operations_control.py
- tests/hermes_cli/test_operations_backup_extensions.py
- tests/hermes_cli/test_operations_extension_lifecycle.py
- docs/build/operations-contracts.md
- docs/build/be14-validation.json
- tui_gateway/contracts/runtime_v1.py (four operational event Literal values)
- apps/shared/src/gateway-contract.generated.ts (generated event union)
- apps/shared/src/gateway-contract.openrpc.json (generated event enum)

**Validation:** Standard wrapper with the existing interpreter:

`HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh tests/agent/test_operations_control.py tests/hermes_cli/test_operations_backup_extensions.py tests/agent/test_effect_reconciler.py tests/hermes_state/test_effect_records.py tests/hermes_state/test_runtime_store.py tests/hermes_state/test_delivery_outbox.py tests/tui_gateway/test_runtime_effects_rpc.py tests/tui_gateway/contracts/test_runtime_v1.py tests/tools/test_individual_memory_store.py -q`

The saved output is `/tmp/be14-validation.txt`: 107 passed/9 files in 11.0s.
Lifecycle/operator/general plugin regression: 119 passed/5 files in 9.8s,
`/tmp/be14-lifecycle-validation.txt`. Affected category/memory/model/entrypoint
loader regression: 35 passed/9 files in 1.7s,
`/tmp/be14-plugin-regression.txt`. Final checkpoint qualification/BE13 retained-
store inventory additions are rerun with the new operator/lifecycle tests in
`/tmp/be14-final-focused.txt`: 24 passed/2 files in 1.7s. Exact commands, counts,
log SHA256 values and pending integration are in docs/build/be14-validation.json.
Ruff for all BE14 Python files/tests and `git diff --check` passed. No dependency
installation, live call, deployment, real deletion, persistent key, training,
staging, commit or push was performed by this worker.

**Event integration completed:** after BE13 checkpoint
cb69a57bd8310fa04c5f4eb8e72d0d518e506ab9, added the four event kinds to
`RuntimeEventEnvelope.type` and regenerated the shared TypeScript/OpenRPC
contracts using `.venv/bin/python scripts/gen_gateway_contracts.py`. A real
transcript erasure and lease revocation produce all four journal event kinds;
the new replay test passes them through the actual wire projection and typed
serialization, proves operation correlation survives, and proves private payloads
do not. Final event/operator/contract-freshness check: 23 passed/3 files in 2.8s,
`/tmp/be14-event-final.txt`. All pending code checks are settled. Live acceptance
gates below remain deliberately unclaimed.

**Additional guards:** read-only checkpoint restore qualification verifies actual
schema/config/policy/runtime/projection versions, watermark and replay retention,
but still refuses history mutation. BE13 bounded-service blobs/receipts, channel
bindings, delegation roots/handoffs/workspaces and BE12 schedule/monitor/commitment
copies are explicitly retained; scoped counts expose their presence without
payloads. Incomplete service pipelines/delegations block transcript erasure.

**Unresolved acceptance gates:** no checkpoint-history restore mutation; no live
database/filesystem encryption; no approved production key custody or retirement;
no full-store/full-profile disaster recovery or upgrade/downgrade proof; no remote
provider/harness deletion/backup expiry proof; no publisher-authenticity or actual
environment reproduction proof; pinned category/entrypoint activation remains
intentionally unsupported/fail-closed; other running plugin processes require a
drain/restart to revoke; no macOS/Windows/daemon-client portable deployment certification
or production-scale contention proof. No distributed storage or training added.

**FE/rollback linkage:** FE11/Dots needs authenticated operator preview/approval,
partial receipt and uncertainty rendering; no Dots files changed. Old readers must
understand added event kinds. Drain work and preserve compatible complete profile
backups/code/keys/manifests before downgrade; backup recovery does not imply schema
rollback. See docs/build/operations-contracts.md for the concrete runbook.

Parent validation: shared TypeScript passed (`npm run typecheck --workspace @hermes/shared`); exact log SHA-256 is retained in the phase validation receipt. Phase base is verified BE13 `a35020dede80abe3871d6d72f9c146225cff6773`.

## 2026-10-03 BE15 — Typed decisions, bounded shadow consumers and secured-node protocol

Implementation checkpoint on base 283163a719c25666c36a35da3fd513bb7d7aa532. Default off; no schema migration, model install/training/download, live node/deployment/security-setting change or paid API. Parent owns direct-main commit/publication.

Before: no typed non-generative decision registry/client, no independently bounded classifier circuit, and no redacted per-point decision receipts on the canonical runtime wire.

After: sixteen versioned point contracts with owners, closed questions/options, unclear, compact state builders, exact digests, effect thresholds, executable synthetic protocol fixtures and declared incumbents. Independently controlled off/shadow/advisory modes; SDK enforce requires exact point/model/calibration/contract/threshold evidence gate and durable receipt, and can only return a recommendation. Production enforce remains unavailable. Model outputs never grant tools, identity, approval, memory, budgets or egress.

Real opt-in core consumers: lifecycle pre_api_request (DP16 first initial attempt), pre_tool_call (DP06 destructive), transform_tool_result (DP07 content), background-review core seam (DP10), and immutable finite-source metadata seam (DP11). DP11 is not a semantic inbox scorer and does not choose notifications. Incumbent tools/prompts/results unchanged. Observation binds existing run/lease/profile, charges actual wall time on BE03 ledger without main-provider attempt/retry changes, and writes metadata-only decision.observed/decision.outcome records. Typed replay exposes only validated receipt/label payloads. Profile A→B→A tests prove no owner/client reuse.

Security: dedicated finite classifier worker slots and per-point circuit/deadline with no retries. Hung calls retain slots, late results never apply. Bounded optional receipt wait; mandatory durable sink failure withholds recommendation. Private packets remain blocked by actual BE14 privacy qualification and still require a future destination-bound authorization implementation. LAN transport requires literal RFC1918 addresses, mutual TLS and certificate pin before body transmission, bounded JSON and exact bundle/registry pins; no proxy/DNS/redirect or insecure fallback. Generic node handler validates local artifact bytes, peer allowlist, finite queue and metadata health, but needs a separately reviewed bounded HTTP/TLS host and external vendor adapter.

Validation (canonical runner): 191 passed across 14 affected/backend-budget regression files; after final per-effect/fixture binding additions, 31 passed across client/actual-runtime/mTLS/generated-contract files. Final independent mTLS benchmark gate 5 passed. Shared TypeScript, Ruff, generated contract freshness and diff checks passed. Every registry question exercises synthetic unclear fallback. Arithmetic calibration fixture: 58 passing metric/provenance/leakage tests. Exact log hashes, source hashes, synthetic evaluation and measured local TLS end-to-end/sequential-batch samples are in docs/build/be15-validation.json. The local TLS numbers are x86_64/container fixture measurements, never Jetson or model claims.

Cross-links: docs/build/typed-decisions.md, docs/build/decision-node-runbook.md, evals/decisions/README.md; FE12 consumer fields in tui_gateway/contracts/decisions.py plus runtime_v1/methods_runtime and regenerated apps/shared contracts. Exact stage whitelist /tmp/be15-files.txt; parent should not stage agent/decisions directory because BE16 owns sibling files.

Remaining release dependencies: actual initial intention dataset is absent; no trained/calibrated Router exists. Inspect its schema/labels/license and heldout suitability before training. Verify actual Jetson/JetPack/serving/model support, deployment allowlist/encryption/auth, actual latency/memory/thermal/backend parity and restore/rollback before node qualification. BE14 private-data lifecycle gates and explicit destination authorization remain closed. Catalog points are not sixteen models or a completion prerequisite; Guard optional later. Per-point measured promotion remains BE16/BE17 work. No raw harness documents or secrets were exported, persisted in receipts or added to this change.

# BE16 proposed buildjournal entry

## 2026-10-03 BE16 — Independent LAYA policies and recoverable front-door planning

- Before: BE15 had typed, default-off/shadow observers and closed receipts; per-point deterministic floor adapters, an evidence-bound release workflow and causal tool-plan/cache recovery machinery were absent.
- After: all DP01–DP16 have tested floor adapters. Five real production observer points remain DP06/DP07/DP10/DP11/DP16; the other eleven explicitly remain adapter-only. DP16 now observes need/effort/family then conditional tool/verification stages, with default-full fallback, authorized bridge retention, immutable context bundles and owner-bound omission/recovery diagnostics.
- Shipping state: all settings remain unchanged/default-off; production enforcement config is rejected. LAYA experiments, activation and promotion are paused per the latest user direction. No training/download/paid provider/live private packet/config activation was performed.
- Release workflow: point-specific pinned contract/model/calibration/service/policy/scope evidence, independent frozen holdout/calibration/safety/latency/budget reports and exact operator approval; synthetic fixtures cannot qualify. Individual rollback retains reason and matched bundle. Corrections cannot raise permissions, tools or budgets. Missing classifications retain point-specific floors only within the qualified rollout scope.
- Cache/authority: same-context schemas remain byte-stable. New bundle installation is a tested component only at a compatible new-context/compression boundary and is not wired for production enforcement. Existing tool-search/describe/call and policy dispatch remain authoritative. Shadow misses are marked observation_only; denied names are hashed.
- Initial candidate: DP16 intention/front-door routing. No intention-routing dataset found in checkout filename inventory; actual schema/labels/license/quality, training/calibration/holdout/shadow/hardware qualification remain pending. No sixteen-model requirement or empirical benefit claim.
- Wire/FE12: closed decision.tool_plan, decision.policy, decision.planner_miss payloads are projected through real runtime replay and regenerated TypeScript/OpenRPC contracts. Operator UI/authorized promotion endpoints and other eleven owner consumers remain deferred.
- Bounded review fixed atomic rollback reads, out-of-scope fallback leakage, mandatory outage fallbacks, and partial/unclear planner stages. No remaining confirmed review defect; real owner integration validated separately.
- Regression repair: initial broader run was 133 passed / 1 failed because a BE05 test still rejected memory schema admission after BE08 certification. With explicit parent authorization, the exact test now verifies admission vs unadmitted execution, no writes and no personal-MCP access. No authority module changed. Settled rerun: 134 passed / 0 failed.
- Validation receipt: docs/build/be16-validation.json; runbook: docs/build/laya-point-policies.md. Counts overlap and must not be summed.

### Exact final validation commands and logs

- focused_owner_planner_policy_and_generated_contracts: passed
  Command: `HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh tests/agent/test_decision_planner_runtime.py tests/agent/test_decision_runtime.py tests/agent/test_decision_point_policies.py tests/agent/test_decision_tool_planner.py tests/agent/test_decision_planner_evaluation.py tests/tui_gateway/contracts/test_generated.py -q`
  Log: `/tmp/be16-integration.log` SHA-256 `977cc8e9b6a6392f52ea2beb135fcc628979cba5d0ed9cc0f340ec29baca5570`
- broader_authority_bridge_and_replay_regression: historical_failure_resolved
  Command: `HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh tests/tools/test_tool_search.py tests/tools/test_tool_search_multiquery.py tests/tools/test_agent_policy_boundaries.py tests/tools/test_capability_broker.py tests/agent/test_tool_view.py tests/agent/test_tool_view_construction.py tests/tui_gateway/test_runtime_rpc.py tests/tui_gateway/contracts/test_runtime_v1.py -q`
  Log: `/tmp/be16-regression.log` SHA-256 `03afe868c2d49b3b5c3545c75c1c66fbf92a30066acba8c89dd0b5fe7ba28876`
- settled_broader_authority_bridge_and_replay_regression: passed
  Command: `HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh tests/tools/test_tool_search.py tests/tools/test_tool_search_multiquery.py tests/tools/test_agent_policy_boundaries.py tests/tools/test_capability_broker.py tests/agent/test_tool_view.py tests/agent/test_tool_view_construction.py tests/tui_gateway/test_runtime_rpc.py tests/tui_gateway/contracts/test_runtime_v1.py -q`
  Log: `/tmp/be16-regression-settled.log` SHA-256 `4e66607481d717e942344395670e7d822fab4ec7856181b70c0b48554d0699d5`
- shared_typescript: passed
  Command: `node_modules/.bin/tsc -p apps/shared --noEmit`
  Log: `/tmp/be16-shared-typecheck.log` SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- ruff: passed
  Command: `.venv/bin/ruff check agent/decisions/{integration,point_policies,point_adapters,release_gates,tool_planner,planner_runtime,planner_evaluation}.py hermes_state_runtime.py tools/tool_search.py tui_gateway/contracts/decision_plans.py tui_gateway/contracts/runtime_v1.py tui_gateway/methods_runtime.py tests/agent/test_decision_{point_policies,tool_planner,planner_evaluation,planner_runtime}.py tests/tools/test_agent_policy_boundaries.py`
  Log: `/tmp/be16-ruff.log` SHA-256 `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18`
- diff_whitespace: passed
  Command: `git diff --check`
  Log: `/tmp/be16-diff-check.log` SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Exact file whitelist

- `agent/decisions/integration.py`
- `agent/decisions/planner_evaluation.py`
- `agent/decisions/planner_runtime.py`
- `agent/decisions/point_adapters.py`
- `agent/decisions/point_policies.py`
- `agent/decisions/release_gates.py`
- `agent/decisions/tool_planner.py`
- `hermes_state_runtime.py`
- `tools/tool_search.py`
- `tui_gateway/contracts/decision_plans.py`
- `tui_gateway/contracts/runtime_v1.py`
- `tui_gateway/methods_runtime.py`
- `apps/shared/src/gateway-contract.generated.ts`
- `apps/shared/src/gateway-contract.openrpc.json`
- `tests/tools/test_agent_policy_boundaries.py`
- `tests/agent/test_decision_planner_evaluation.py`
- `tests/agent/test_decision_planner_runtime.py`
- `tests/agent/test_decision_point_policies.py`
- `tests/agent/test_decision_tool_planner.py`
- `docs/build/laya-point-policies.md`
- `docs/build/be16-validation.json`

Validation JSON SHA-256: `c4fc59c83703fc6aad798ed888be902d34898e5cff4bdd318b65c4d819380533`

No Git staging, commit, push, branch operation or root buildjournal edit performed by this worker. Parent owns publication and remaining BE18 validation.

Publication: local phase checkpoint. Standard Git push is awaiting existing remote authentication; no alternate publication route is being used. LAYA activation and rollout decisions remain deferred.

## BE17 — governed dataset and model-release tooling (2026-10-03 UTC)

Status: local code/CLI governance implemented and verified; real training and production acceptance gates remain deferred. No trained candidate is claimed. Remote publication remains pending existing Git authentication.

Before: the phase roadmap specified dataset provenance, deletion propagation, frozen splits and signed release gates, without a concrete local CLI consumer. After: bounded consent/purpose/scope records, independent-label checks, secret scrubbing without authority truncation, task/episode/time/content anti-leakage, deterministic receipt-only exports, immutable holdout lineage, source deletion/invalidation/checkpoint-review receipts, offline scalar calibration refit, Ed25519 existing-public-identity verification, opaque artifact hashing, independent shadow metric gates, operator bundle selection and complete predecessor rollback are executable locally. Guard/router drift produces a hold/investigation/proposal only.

BE15 integration: manifest contract versions and exact semantic digests/questions are pinned; `runtime-projection` validates installed `contract_for` entries and constructs the existing `ModelBundle(model_digest, calibration_digest, service_digest)`. This neither installs a calibrator nor activates serving. The explicit synthetic fixture uses the actual DP05:v1/scope menu and is a plumbing fixture, not the promised post-backend harness dataset.

Migration/flags: no DB migrations, gateway/config/default changes, credentials, keys, or runtime enablement. Default export omits packets. Private records need existing source/purpose/scope/destination-specific approval; including packets needs explicit inclusion approval. Only tests create mock checkpoint bytes and temporary signing keys. No actual dataset export, external host, model download, remote teacher/GPU or training run occurred.

Validation:
- `HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh tests/evals/test_decision_governance.py tests/agent/test_decision_calibration.py tests/agent/test_decision_client.py -q`: 106 passed / 0 failed, 3 files; 30 are BE17 tests. `/tmp/be17-validation.txt` SHA-256 `e55fc5e6d5303e9c3cd0ec1c72fdcd7231453b7b8f9cdee5a7b6976fb6f90c54`
- Synthetic CLI rebuild twice against the same ledger: manifest `ef01647868fd52ef48053d0d339822dddcc49b1ad0d2bf7724e50c04606f1d02`, identical export SHA-256 `51dd0ee52b0f64461e1db325c117d8de25b337d6f16dbb4a9f47cef87d60149a`; 3 records, no packets. `/tmp/be17-cli-rebuild.txt` SHA-256 `f6f776105ac5d535f7e109f95976d213179a320b2c25886c913f8897692b8c7d`
- Actual CLI offline refit: calibration digest `e862b9f9c4179f715b067f913c0b928655880e7f5ae26aea8fc951ab6f01d310`, explicitly untrained/unqualified. Deletion rebuild yields 2 rows with deleted source absent, manifest `8c8a2e9f78d3dfbfc344bfec2c24033c4dcf257b77a727865eafeef6c616f2db`. `/tmp/be17-cli-refit-deletion.txt` SHA-256 `d1cefc17e1e7bcd35621c0f14c24bdd06b5f92b6920995075e4d260794779a64`
- Actual `deletion-impact` and Guard `drift` CLI receipts confirm source-manifest invalidation and review hold without training/privilege relaxation. Signed mock tamper, two independent exact fixture builds, actual runtime projection and rollback paths are exercised inside tests only.
- Ruff on the three new modules plus their test passes. `git diff --check` passes. Full suite remains the BE18 gate.

Evidence: [docs/build/be17-validation.json](docs/build/be17-validation.json); runbook/boundaries: [docs/build/decision-data-governance.md](docs/build/decision-data-governance.md). BE15 companion paths: `agent/decisions/contracts.py`, `registry.py`, `calibration.py`; those were not edited by BE17.

Deferred: actual intention dataset inspection, the separately approved post-backend harness dataset, exact RLCD source/license/toolchain and target hardware investigation, real model training/reproducibility, measured serving/token limits, production holdout/red-team qualification, installed calibration, live shadow promotion and serving rollback. Signing proves integrity/provenance, not model safety; removing source records does not unlearn existing weights. Supplied approvals and evaluator identities are controlled operator attestations, not an authentication service.

Checkpoint base: `a815882c42c5eaec3155845494be70bca40529d0`. Real LAYA experiments, activation and rollout decisions remain deferred.

## 2026-10-03 FE00 — Existing-surface runtime contracts and controls

Status: verifying; locally implemented consumer foundation, full platform gate pending.
Source: FE00/U32, BE02 generated runtime contracts. Base: 0a4621c6.

Implemented a shared typed runtime controller using generated RpcMethods and a discoverable
TUI `/runtime` command (inherited by the dashboard's existing PTY TUI). Status, refresh,
bounded event replay, steer, cancel and explicit identical-command retry use the existing
owned session transport. Capability/version negotiation gates controls; generation checks
prevent stale selection writes; disconnected transport leaves work stale rather than cancelled.
Stable command IDs and expected revisions guard duplicate submissions and uncertain retries.
Receipt acceptance is explicitly separate from execution, artifact validation and delivery.
Added a surface/authority inventory; no second chat, scheduler, memory or policy engine.
Classic CLI and Electron-specific runtime controls are not claimed implemented in this checkpoint.
No new Python registry advertisement on surfaces without a handler.

Validation: root `node_modules/.bin/vitest run apps/shared/src/runtime-control.test.ts
ui-tui/src/app/runtime/runtime-command.test.ts` passed 4 tests/2 files. Shared workspace
`npm run --workspace apps/shared typecheck` passed. `git diff --check` passed.
TUI aggregate typecheck initially failed due missing dependencies. Lock-respecting staging
`npm ci --ignore-scripts --cache /workspace/shared/ryoko-npm-cache --prefix /tmp/ryoko-frontend-deps`
succeeded (1331 packages); missing dependency files hydrated without overwriting existing files.
`npm run --workspace ui-tui build:ink` and `npm run --workspace ui-tui typecheck` then passed.
The typed adapter spreads generated params into the transport dictionary without weakening the
wire contract. No lockfile/dependency version/live configuration changes. Native install scripts
remain disabled. Browser/native visual
QA, live identity binding, real provider/hardware and classic CLI parity are not claimed.

Rollback: remove the additive view/command and shared export; authoritative runtime work remains.
No storage migration or credentials. Publication: normal Git HTTPS push unavailable (no credential);
this phase remains a local checkpoint until authenticated publication is possible.
Next: FE01 identity/memory/capability health rendering and existing Electron inspector; resolve
workspace dependency gate for aggregate frontend verification.

## 2026-10-03 FE01 — Identity and actual capability health inspection

Status: verifying; local supported inspector complete, live setup and full provenance gates pending.
Source: FE01/F34, BE01/04/05/08. Base 91fd20f8.

Added `/runtime identity` and a Runtime & memory inspector in Electron’s existing Command Center → Maintenance. The view uses the exact active gateway/session, never opens another chat, and probes real `runtime.capabilities` and `runtime.memory.status` on explicit refresh. It shows declared backend/execution/cancellation limitations, frozen tool policy counts, queue scope, primary-only personal MCP versus isolated built-in memory, acknowledged capabilities and repair guidance. Failed probes cannot show connected/ready or leak error payloads. Scope keys/unmount generation invalidate stale results; disconnect disables refresh and retains honest stale work status. Existing maintenance controls are preserved. Chrome labels cover bundled locales; backend diagnostic text remains original.

Checks: shared identity and TUI consumer tests 4 passed/2 files; Electron inspector behavior test 1 passed; full TUI typecheck passed; Electron renderer `tsc -p . --noEmit` passed in clean FE00+FE01 validation worktree, as did clean shared/TUI typechecks (future modules excluded); whitespace check passed. Exact logs/hashes in docs/build/fe01-validation.json. Tests use controlled transports, not real MCP/hardware. Full redacted override provenance has no current backend projection; credential entry/grant editing and two real setup flows are not fabricated. No live/native visual claims.

Rollback is additive inspector removal; no state migration, persistent client cache, credentials or permission authority added. Local commit only until authenticated normal Git publication is available. Next FE02 project continuity/capture/scoped acknowledged corrections.

## 2026-10-03 FE02 — Project continuity, capture review and scoped corrections

Status: verifying; supported local controls implemented, harness/indexing/product journey gates pending. Base 5b328595. Source FE02/F01/F03/F04/F05/F06; BE07/BE08.

Added concrete project list/create/inspect/select/resume and exact-revision canonical attachment commands; old artifact refs are preserved. Resume presents actual current versions, stale dependencies, mission states, evidence authority/freshness and blockers, without launching work or claiming complete history. Capture commands retain original artifact IDs/versions and extraction failures, allow revision-checked filing/unfiling, and show duplicate candidates without consolidation. Explicit remember/correct/forget controls retain scope and expected version; acknowledgment/conflict and tombstone limitations are separate. Primary harness unavailability never routes to built-in memory.

Existing TUI `/runtime project|memory|capture` and Electron Runtime & memory inspector share these concrete handlers. The expert command panel requires explicit submission, blocks double clicks, uses escaped text, clears output on feature/context changes, never retries unknown mutations automatically, and closing a view never cancels accepted work. Actual consumer tests cover delayed TUI memory after conversation change and delayed Electron identity probe after gateway/session switch. No competing project/memory database or scheduler.

Validation: focused shared/TUI 6 tests passed; desktop consumer 3 tests passed; clean-phase shared, TUI and Electron renderer typechecks passed. Exact commands/log hashes in docs/build/fe02-validation.json. Later uncommitted phases excluded from validation. Backend authority remains independently tested by backend phases; transport fixtures are not live harness certification.

Deferred: actual personal MCP operations pending configured server/schema/secure credentials, capture indexing/approximate retrieval/batch/mobile adapters, complete product pilot and polished domain-specific forms. No full P01/P02/P03/P06 acceptance claim. Additive rollback removes UI only; backend versions/receipts retained. Local-only Git checkpoint; next FE03 complete artifact bytes, revision review, templates and alternatives.

## 2026-10-03 FE03 — Complete artifacts, exact revisions, templates and comparison

Status: verifying; usable Markdown controls implemented, broader quality/platform gates pending. Base 676f8bad. Source FE03/F10/F11/F32; BE06/07/10.

Added typed Electron artifact workbench in the existing runtime inspector: create complete Markdown, load exact immutable version with full-byte SHA-256 verification, choose unambiguous heading outside code fences, review baseline/replacement side by side, prepare exact section digest/base/head preconditions, and confirm exact publication separately. Edited inputs invalidate approval while retaining proposal identity; Discard calls exact backend cancellation and waits terminal acknowledgment before freeing the UI for another prepare. It never claims committed effects were undone. Control ID is visible for recovery after closing the view.

Added immutable version comparison with no implicit selection/overwrite, typed template creation from an approved baseline with structure/style/topic slot and explicit incidental-content exclusions, and complete-download controls. Electron reads no partial file into a link, cancels pending reads, uses inert application/octet-stream download bytes, and never previews active HTML. TUI explicit local download uses exclusive create (no overwrite) after digest verification. Shared advanced artifact/template controls preserve exact approval/request/content binding; structured TUI input preserves whitespace inside JSON content.

Validation: shared/TUI 8 tests passed; desktop 5 tests passed; clean shared/TUI/Electron renderer typechecks passed; actual canonical artifact RPC suite 10 passed. Tests include complete chunks/truncation, unchanged baseline, exact approval/content revision, cancel before reprepare, explicit file save/no overwrite, and cancel without partial link. Log hashes/commands in docs/build/fe03-validation.json. Existing server independently validates section locks/digests and scope.

Limitations: Markdown-first, no format-layout/fidelity claim; polished branch-merge chooser and three-varied-topic template proof remain pending. New detailed forms are English pending complete localization/visual/a11y consolidation. No real remote provider/hardware/native Electron acceptance; no full F10/F11/F32 claim. Additive rollback retains authoritative immutable versions and control/effect receipts. Local-only publication. Next FE04 typed mission progress, evidence acceptance, precise cancellation and delivery recovery.

## 2026-10-03 FE04 — Conversation-owned mission progress and review

Status: verifying; supported typed control/review surface complete, live product pilot pending. Base 0459d064. Source FE04/F02/P07; BE03/06/09.

Added a user-invoked Mission dialog on the actual primary conversation surface, with concise outcome/state/next-step and timestamped snapshot summary. It reads the exact provided live gateway/session, refreshes on that session’s message/result events and explicit mission actions, and never steals focus for background events. Switching scope closes the old dialog; delayed old summaries cannot appear in a new conversation. Existing approval overlays remain untouched.

Typed mission panel creates reviewed intent, revises exact current revision, attaches multiple immutable artifact outputs and optional required Markdown sections, preserves unrelated deliverables, runs actual deterministic verification, and explicitly confirms acceptance only from current ready-to-review evidence. Pause/resume/cancel and missed corrections preserve authoritative states; cancellation does not undo effects. Unknown mutation failure requires refresh before more writes. Execution/acceptance/delivery, partial outputs, blockers and receipt evidence remain separate.

Added delivery inspection and retry-existing-notification controls, never mission rerun or manual false client receipt acknowledgment. Advanced TUI mission/approval/delivery commands reuse typed contracts; metadata-only approval view supports denial but cannot approve unseen content. Exact-content artifact approval remains in its original review. Visited typed inspector panels stay mounted when hidden so temporary tab changes do not discard pending review state.

Validation: shared/TUI 5 tests passed, desktop 4 tests passed including real ChatView integration and delayed old-scope summary, clean shared/TUI/renderer typechecks passed, canonical mission RPC suite 15 passed. Log hashes/commands in docs/build/fe04-validation.json. No live-provider/two-output quality pilot, native Electron or full delivery certification claimed. Detailed-form localization and aggregate visual/a11y gate remain FE13. Additive rollback removes controls without deleting authoritative missions/effects. Local-only Git checkpoint.

## 2026-10-03 FE05 — Retained-source research and reviewed brief refresh

Status: verifying; bounded typed evidence/brief UI implemented, connected source and decision-lab gates pending. Base 88733e53. Source FE05/F07/F08/F09; BE07/09/10.

Added real typed research form to existing Electron runtime controls and TUI research commands: select retained project artifact or capture-original IDs, exact versions/digests/byte ranges/quotes, observe missing/stale/incomplete coverage, and separate source claims from interpretations. Initial dependency baselines derive owner scope from actual server source reads rather than caller identity. A stable memoized transport retains exact timestamp-bearing preparation bytes; saved-manifest refresh can resume without client storage authority.

Brief/manifest refresh displays both exact ordered artifact approval records and requires explicit review plus canonical confirmation before publication. Changed inputs or disconnect revoke approval while preserving command identity; Discard/status use exact backend command and terminal receipts before allowing new preparation. Partial/non-atomic publication and unknown outcomes remain explicit; no automatic retry, false complete brief or private scope leak. A pre-dispatch validation failure clears only local phantom preparation without sending cancellation for nonexistent backend work.

Split large initial component into topical locale/types/input/action/view modules before checkpoint; main component is below300 lines and rendering/action functions stay bounded. All nine bundled locales have chrome.

Validation: shared research4 tests and rendered research12 tests passed; clean shared/TUI/renderer typechecks and scoped ESLint passed. Exact logs/hashes in docs/build/fe05-validation.json. Existing canonical BE10 research contracts were also worker-tested, but no new live-service proof is claimed.

Limits: retained local sources only, no connected-search P08 claim, typed form one claim update and one citation range per source, no scenario/decision engine or automatic monitoring. Prior brief versions and unrelated claims stay backend-owned. No native/browser visual or complete product-quality pilot yet. Local-only checkpoint; next FE06 manual workflow and truthful finite schedule/monitor controls.

## 2026-10-03 FE06 — Manual workflow runs and truthful finite monitor health

Status: verifying; supported manual workflow/record-only schedule controls implemented, live monitoring gates pending. Base 005e6658. Source FE06/F19/F20/F21; BE11/BE12.

Added typed WorkflowPanel and TUI workflow/schedule actions over existing contracts. Inspected immutable approved workflow versions expose declared inputs/outputs, permissions/evidence and real typed scalar parameter fields. Explicit run preparation, ordered exact artifact review and canonical confirmation publish only the chosen version and parameters. Accepted-mission save, definition authoring/evaluation/feedback and bounded history remain supported through inspected text commands, without a second workflow engine.

Edited inputs, scope switches and disconnect revoke review but retain the original pending command. Explicit status/cancel uses exact command identity; only terminal acknowledgment releases preparation. Unknown cancellation cannot silently unlock another run. Schedule inspection displays active/paused/expired/revoked, actual health, last success/error, next due, expiry, timezone/DST and finite check budget. Baseline/no-change/source-failure/matched-change states stay distinct. Pause/resume uses current revision and backend scope; no default standing grant or hidden notification send.

Validation: shared workflow16 tests and rendered workflow8 tests passed; clean shared/TUI/renderer typechecks passed. Worker scoped lint/type verification also passed. Exact logs/hashes in docs/build/fe06-validation.json. No live source/notification pilot claimed.

Backend limits are visible: record-only finite local monitors, created paused; no snooze/quiet-hour/digest or notification dispatch API. These controls do not pretend checks stopped while delivery is merely suppressed, or fabricate end-to-end P10 alert precision. Graphical workflow canvas remains intentionally deferred; typed scalar rerun is usable now. Local-only checkpoint with backend state/occurrence receipts retained on rollback.

## 2026-10-03 FE07 — Typed data and prompt-only creative packages

Status: verifying; two selected local production packages implemented, broader domain and fidelity gates pending. Base ae35f64f. Source FE07/F12/F28; selected Slice-D scope permitted by plan.

Added typed data/creative desktop form and TUI domain controls over canonical backend adapters. Retained CSV/XLSX IDs/versions/digests, encoding, delimiter, date/currency/null/duplicate/unit assumptions, aggregation, chart and export inputs are explicit. Complete immutable manifest readback verifies digest and chunk boundaries before showing data profile, recipe and selected-row lineage; no raw source cells are dumped. Formula caches are declared preserved, not recalculated.

Creative mode separates brief/prompts/continuity and supplied reference/rights declarations; prepare/publish creates prompt-only package with zero image/audio/video generation. Rights are not certified and publication is not external production/delivery. Exact ordered output approvals are reviewed, with manifest last and explicit non-atomic publication confirmation.

Stale inputs/disconnects retain original control identity and revoke approval. One-shot discard waits terminal acknowledgment; uncertain publication is inspected, never automatically retried. Local byte-bound rejection occurs before reserving a phantom lease. Fixed manifest readback to pass only the exact artifact/version/digest pin, and kept the confirmation owned until the actual request settles. Effect cleanup captures its exact lifetime object. Nine locale chrome sets are included.

Validation: shared domain4 tests, actual desktop23 tests, clean shared/TUI/renderer typechecks and scoped lint (zero warnings) passed. Exact logs/hashes in docs/build/fe07-validation.json. Earlier test failures (manifest pin normalization/confirmation lifecycle and a copy key) were corrected and affected tests rerun.

Limits: typed data UI starts with one retained source; advanced commands expose supported joins/multiple inputs. New upload adapter, lesson/tutor/demonstration packages and live media generation are explicitly deferred. No workbook visual fidelity, recalculation, learner transfer or production-quality pilot claim. Additive rollback preserves original datasets/media and published artifacts. Local-only checkpoint; next FE08 reviewed obligations/drafts.

## 2026-10-03 FE08 — Reviewed commitments and draft-only correspondence

Status: verifying; supported source-backed review/draft controls implemented, live communications/calendar gates pending. Base 24ff4dc6. Source FE08/F13/F22/F23/F24; BE06/10/12.

Added FollowthroughPanel to existing runtime controls and TUI commitment/correspondence commands. Active review includes accepted nonterminal obligations only. Candidate inspection supports explicit owner/outcome correction and ISO timestamp-with-offset plus IANA zone before exact-revision confirmed acceptance. Leaving an item unaccepted creates no obligation; unsupported durable decline and terminal reopen are stated plainly. Source text never silently invents a speaker, owner or due date.

Typed correspondence fields collect exact recipient identities, draft text and immutable source references, then create a draft only. Canonical returned correspondence IDs are retained for inspection. Edited drafts need new IDs/review; existing confirmed send evidence may be associated through the backend but never sends a message or proves delivery. Advanced controls include imported-inbox evidence, waiting review and source-snapshot calendar preview without calendar writes. Scope switches, disconnects, stale confirmations and repeated clicks remain guarded. Nine locale chrome sets and existing confirmation primitives are used.

Validation: shared follow-through17 tests and rendered panel5 tests passed; clean shared/TUI/renderer typechecks passed. Logs/hashes in docs/build/fe08-validation.json. Source/receipt fixtures are not live inbox/calendar/provider evidence.

Limits: no live meeting speaker attribution, mailbox/calendar check, day/week workload/travel planning, send/queue endpoint or provider/human-read certification. Backend durable decline/reopen APIs remain absent. No automatic reminders, blanket replies or new promises. Rollback retains accepted records/receipts and never retracts sent messages. Local-only checkpoint; next FE09 scoped execution and real-outcome inspection.

## 2026-10-03 BE18 — Validation and regression-repair checkpoint (not release sign-off)

Frozen foundation: 0a4621c6b8eb69d170ad4bf5e63aabfb5946b8d0, tree b4e16d1a88147f456087a0cf6427e8983e24ffce, plus explicit BE18 files. Validation used an isolated detached worktree while frontend changes proceeded independently. Initial source digest stayed unchanged throughout the complete Python campaign.

Before: separate phase receipts had not exposed all cross-phase and inherited-consumer integration failures. After: six finite local release journeys, a producer-only compatibility fixture, failure-preserving evidence tooling, source/config/dependency/schema manifest, and targeted regression repairs are recorded. Real fixes remove obsolete lossy persistence consumers, preserve exact structured media and private provider state through reopen/export/deletion guards, complete schema chronology, retain ownership/uncertainty semantics in legacy consumers, guard corrupt scheduled identity configuration, and route isolated executor lookup through the canonical resolver. Stale admission/tool-view/protocol fixture seams and the synthetic DP05 contract pin were reconciled without weakening production gates or changing training labels/permissions.

Validation:
- Complete default Python campaign: 5,331 files; 55,681 passed, 558 failed, 725 skipped; additional collection/setup errors; exit 1; 3,191.6 seconds. Full log SHA-256 d18882770fd575a90528f8834696c72c97c224fb15c40a0fcae3b906d0043743. This remains a failed original receipt.
- Exact baseline comparison: 243 failing existing files against 4b7268c69f72c3fa5d2d056a3bbce9a3b65d94cd; 3,139 passed, 443 failed, 58 skipped, with setup/collection errors separately retained. No second full suite.
- Final critical authority/effect/memory gate: 29 files, 357 passed, zero failures/skips.
- Final affected campaign: 35 files, 1,378 passed, 16 failed. Fourteen lack aiohttp, one reproduces the inherited /tmp marker, and one stale native-vision assertion was then repaired: three exact gateway cases plus 57 multipart/message-UID consumer checks pass, including real SQLite reopen fidelity.
- Final slice/tooling/generated-contract campaign: 16 passed; after checkpoint gap-gating/sanitization changes, release-tooling six tests pass. Changed Python Ruff (41 files) and diff check pass.
- Shared producer TypeScript and serialized desktop typecheck pass. TUI unit suite: 175 files/1,610 passed. Web unit suite: 50 files/359 passed. Root JS: 180 passed, 11 native-preparation failures, one skipped. Desktop aggregate units were interrupted after prolonged lack of completed-test progress; exact active node was not established. The last reported voice-prefs file independently finishes with 16 passed/two failed; its failures are not claimed as baseline-qualified.
- Across the 245 initially failing files, 37 pass on documented repair/resource reruns. Every remaining observed Python failed node is also represented on the comparison baseline after exact resource/temp-root checks; this is node-level reproduction, not a blanket identical-root-cause claim. Detailed sanitized node/phase/reason classifications and all hashes remain in docs/build/be18-validation.json.

Resource/evidence limits: /var/tmp maps to tmpfs /tmp. Updater fixtures can exhaust it even serially. Original full telemetry lost two files; later reporting writes atomically and preserves pytest's true exit status on ENOSPC. Exact disk-backed reruns and their environmental changes are recorded; original missing evidence is never fabricated. Only completed BE18-created fixture directories were removed after retaining their logs/receipts. Existing lock-respecting Node dependencies were copied from approved staging and frozen local Ink built; temporary dependency fixtures created by inherited tests are separate from runtime hydration.

Remaining implementation work is explicit: local user notifications and their policies, actual template application beyond retained records, output-influence controls, and full-store recovery. These are mandatory code gaps, not merely live-service qualification. This checkpoint does not complete the full BE18 objective or authorize release/cutover.

Other blocked/unqualified areas: optional agent-client-protocol==0.9.0, aiohttp==3.14.3 and fal-client==0.13.1 scopes; absent prepared Electron/native inputs; unresolved complete desktop-unit coverage; live model/harness/connector/browser/voice/hardware acceptance; sensitive-ingestion encryption and production key custody; full-profile recovery/migration; empirical benefit; FE13 and Dots OD00–OD04. LAYA remains default-off, with no model training, live activation or promotion. No real credentials, private user data, Dots changes or production deployment were used for this work.

Rollback: pause admission/schedules, reconcile unknown effects without replay, preserve immutable artifacts/consumed approvals/tombstones, and retain schema-compatible readers. Local temporary-store reopen/effect recovery is demonstrated; production encrypted restore and downgrade are not. One runtime owner and scheduler remain mandatory. Client timeouts and delivery retries cannot restart missions.

Publication: parent integrates the exact checkpoint whitelist and verifies any remote commit/CI separately. Local verification does not assert remote publication or CI success. Subsequent required feature completion needs new affected qualification and eventual release sign-off.

## 2026-10-03 FE09 — Reviewed bounded execution and real outcome receipts

Status: verifying; the supported local execution consumer is implemented, with broader coding/browser/device acceptance still unqualified. Base 5f99cce9; FE09/F25/F26/F27, BE05/06/09/10/13.

Added typed execution review to the existing runtime inspector and /runtime execution. The service view discloses the two actual registered finite document services, capability/health and processing/storage location. Exact retained source versions and digests are prepared before explicit execution; same-source re-preparation preserves request identity so backend stage recovery cannot blindly repeat committed work. Receipts distinguish pending/partial/completed stages, disconnected executors, transfer digests and unresolved effects. Bounded output bytes are checked against size/digest, without dumping private content into status. Session/transport changes invalidate review and reject late output. Closing a view never cancels accepted work.

Validation: 15 shared execution tests and 5 rendered panel tests passed in the phase-only validation checkout. Shared, TUI and Electron renderer typechecks passed there. Exact log hashes and commands are in docs/build/fe09-validation.json. No full-suite, deployment or live executor qualification is inferred from these focused fixtures.

Remaining implementation/acceptance: the current local normalization/structure pipeline is not a transcription-to-document demonstration. Gateway executor choice is unavailable; integrated coding promotion/browser consequential-submit evidence, live device handoff and visual/native qualification remain open. Existing coding/browser controls are preserved rather than duplicated. Execution chrome is English pending final localization review. Local-only checkpoint; next FE10 bounded specialist/media/channel controls.

## 2026-10-03 FE10 — Bounded specialist, media and channel controls

Status: verifying; actual supported roster/channel controls are mounted, broader specialist/team/voice/screen acceptance remains open. Base ca5902f2; FE10/F16/F17/F29/F30/F31, BE08/13.

Added SpecialistPanel and /runtime specialist over the existing owned gateway. Roster inspection preserves failed children and distinguishes visible lineage from current control. Exact-session steer and interrupt do not manufacture delivery or terminal completion. Local verified channel binding and revision-bound logical input submission retain attempted identity after disconnect; no blind resubmission occurs. Specialist memory stays isolated built-in; primary personal memory is never read/shared through the roster.

Media inspection reports actual declared capabilities without starting capture. Speech stop, discard captured audio and accepted-work cancel remain different operations. Advanced bounded snapshot metadata/annotation and exact transcript/selected-text submission reject caller-supplied identity and mismatched payloads. No continuous screen observation, invented OCR, implicit recording or external identity mapping is added. Scope/unmount guards suppress late private output.

Validation: 15 shared specialist tests and 4 rendered tests passed in the clean phase-only checkout. Shared/TUI typechecks passed. Renderer typecheck first exhausted Node's default 2GB heap, then passed with a bounded 4GB heap; both logs are retained in docs/build/fe10-validation.json. Existing canonical backend fixture evidence is documented separately in frontend-execution.md and is not a live-device demonstration.

Remaining implementation/acceptance: no named specialist manifest/team-budget gateway controls, microphone/playback consumer, trusted capture acquisition timestamp, or actual cross-device/channel handoff qualification. Configured local voice adapter construction is pending independent backend verification and must never silently download models. English chrome/localization and visual/native qualification remain open. Local-only checkpoint; next FE11 status/setup/repair projections.

## 2026-10-03 FE11 — Current work, unresolved effects and privacy capability status

Status: verifying; supported bounded status/repair projections are implemented, opportunity and broader setup acceptance remain incomplete. Base d3e6224c; FE11/F18/F19/F28/F35.

Added a typed Current work and repair panel and /runtime overview. The view reads canonical ready-to-review, waiting and active missions, selected-project upcoming schedules and accepted commitments, unresolved effects and capability/memory health. Independent failures remain visible; an empty or failed bounded queue is never an all-clear. Effect inspection and explicit local-evidence reconciliation do not replay external mutations. Privacy view reports backend capability truth and explains tombstones versus physical erasure without pretending a hidden row or request is deletion acknowledgment.

Validation: two shared status tests and two rendered tests passed in the clean phase-only validation checkout; shared/TUI/Electron renderer typechecks passed (renderer 4GB heap). Scoped helper/panel lint passed with no warnings. Session changes discard late results; merely opening or inspecting the panel never repairs effects or creates tasks. Exact command/log hashes in docs/build/fe11-validation.json.

Remaining implementation: durable scoped opportunity proposals/dismissal, authorized setup/repair plans and bulk export/retention/deletion manifests have no exposed gateway operations here. The view says so; these are code-level requirements, not merely unmeasured live gates. Full live harness/setup journey, native, keyboard/visual and product-effort qualification remain pending separately. Local-only checkpoint; next FE12 read-only recorded decision explanations with off/shadow defaults unchanged.

## 2026-10-03 FE12 — Read-only recorded decision explanations

Status: verifying; receipt explanation view implemented with modes and deterministic floors unchanged. Base a02eab31; FE12 and decision receipts from BE15/16/17.

Added Recorded decisions panel and /runtime decision inspect over existing bounded durable replay. It explains actual recorded mode/route/fallback, verified tool-family plans and recovery/outcome signals in plain language. Shadow observation is explicitly not control authority, a confidence signal is never permission, and no raw packet or private chain-of-thought is shown. Replay gaps and pagination are visible; absence in a bounded page does not establish service activation/inactivity. Session/transport changes suppress late receipts. The control is read-only and cannot activate LAYA, train a model, change channel policy or weaken an approval floor.

Validation: two shared receipt tests and two rendered tests passed in the phase-only clean checkout. Initial shared typecheck caught an incomplete synthetic receipt fixture; the fixture now supplies every generated field and shared/TUI/renderer typechecks pass. Logs including the original failure are hashed in docs/build/fe12-validation.json. Scoped panel/helper lint passed.

Remaining implementation and qualification: correction/feedback/override, mode/ambient opt-in and proven-safe approval reduction APIs are not exposed. No user-friction, latency, variable model quality, dataset or trained-model deployment claim follows from this view. Off/shadow defaults and all deterministic authority/evidence gates stay intact for the user's later review. Local-only checkpoint; next FE13 closes feasible local UI gaps and consolidates acceptance evidence, then FE14 read-only Dots handoff documentation.

## 2026-10-03 BE18 follow-up — Verify actual storage-denial injection

Reproduced the two unresolved desktop voice-preference failures (16 passed, 2 failed). The test injected quota/security failures on the Storage instance; jsdom calls were not intercepted. Bind the fixture to Storage.prototype and assert it was actually called. All 18 cases now pass; production preferences code is unchanged. Exact before/after logs in docs/build/be18-desktop-fixture-followup.json. This does not replace complete desktop aggregate or native voice acceptance.

## 2026-10-03 BE07/BE11 completion — Apply canonical approved templates

Saved examples now have exact immutable pins and actual bounded Markdown preview, preparation and approved publication. Explicit slots, exclusions, applied formatting versus advisory style, lineage-only assets and selected section locks are preserved. Workflow execution uses the same canonical template renderer; old style-only metadata stays inspectable but cannot falsely claim application. Three varied-topic and old-pin/lock proofs pass. No model call or external publication.

## 2026-10-03 BE08/FE02 completion — Inspect supplied references and control future context

Schema43, following monitor42, records exact fresh-memory references actually supplied in successful provider requests. This is explicitly limited coverage, not causal explanation or enumeration of all history. Owned latest-output controls support response-only ignore/correction, scoped suppression and acknowledged same-store CAS correction/tombstone. They preserve metadata, cache prefixes, historical messages and namespace isolation. Uncertain post-store receipts remain pending and cannot silently replay. Primary external harness mutation stays unavailable without verified version/ack schemas; no fallback personal store is enabled.

Template/influence qualification: 133 tests across14 files passed through the required isolated scripts/run_tests.sh runner in32.9s, plus scoped lint and whitespace checks. The earlier direct-pytest receipt is retained but superseded for environment qualification. Exact file/log hashes and limits: docs/build/project-reuse-validation.json.

## 2026-10-03 BE12/FE06 completion — Deliver owned local monitor notifications

Actual registered RPC → existing per-profile cron → BE06 outbox → exact attached local transport now supports quiet hours/DST, bounded digests, expiring snooze, persistent dismissal, immutable dedup and paginated retained notices. Admission and retry recheck state/version/policy/expiry/dismissal and live owner/project authority. Unknown sends remain unknown; delivery retry never reruns a source check or mission. External destinations and OS toast daemons remain unimplemented. Final canonical regression:214 passed across19 files, zero failures/skips; lint and whitespace passed. Receipt: docs/build/be12-notification-validation.json. Client render/ack acceptance follows in FE13.

## 2026-10-03 BE12/FE08 completion — Durable decline and bounded agenda proposals

Added exact owned human decline of nonbinding commitment candidates, preserving reason/revision/actor through SQLite reopen and reimport, with acceptance blocked after decline. Existing accepted terminal obligations remain immutable and cannot reopen. Added source-bound day/week planning over exact supplied availability, explicit daily windows and ordered duration estimates, fixed intervals, buffers, flexible-work capacity and visible overflow. No implicit calendar write, invitation or new promise.

Validation: 14 tests across three canonical runner files passed, including real owned RPC, close/reopen, cross-scope denial, timezone/DST and capacity/overflow behavior; scoped Ruff passed. Receipt: docs/build/be12-agenda-decline-validation.json. Live availability/speaker evidence and typed UI acceptance remain unqualified. This closes local code gaps; it does not certify live inbox/calendar operations.

Integration: these completion sections share additive schema42/43 and generated registrations and are committed together to keep every checkpoint importable. Pausing UI exposure or schedules preserves consumed approvals, unknown effects, immutable records and source pins. Live services, sensitive-ingestion key custody, full-store recovery completion, final combined UI qualification and release signoff remain separate. Local-only; no Git push, live activation or LAYA rollout.

## 2026-10-03 BE14 completion — Bounded owning-store recovery

Before: BE14 backup drills only copied state.db, checkpoint qualification always denied restore, and the operator broker had no restore-checkpoint action. Canonical project stores, artifact bytes and individual memory could not be jointly qualified.

After: a bounded Linux/single-actor owning-store bundle inventories and validates state.db, projects.db, every canonical artifact blob (retaining orphan bytes without promotion), and individual-memory SQLite history/conflicts plus checked projections. It rejects incompatible/unsupported local owning stores and explicitly records excluded config/credentials, external personal-memory providers/project workspaces and non-certified ancillary state. WAL-safe copying reuses the updater copier; the existing updater/full-profile authority remains unchanged. A fresh temporary-home reader drill checks complete file hashes, SQLite integrity/FKs, scope/generation/effect/approval safety, native SessionDB readers and checkpoint reconstruction. The CLI exposes drill-recovery.

The existing preview/authorization/maintenance-lease broker now supports restore-checkpoint. It reconstructs only derived state from the latest compatible checkpoint plus contiguous journal tail using the same canonical reducer as live appends. Current effect/approval/artifact references override stale checkpoint references. Commands, consumed approvals, unknown effects, context/transcript bytes, journal history and owner counters are never rewound; no adapter executes. The cache replacement and required before/after events are one writer transaction. Real child-process death mid-repair proves rollback, while lease generations remain monotonic.

Validation: HERMES_PYTHON=/workspace/scratch/42f2baf55663/ryoko-agent/.venv/bin/python HERMES_HOME=/workspace/shared/ryoko-dev-home HERMES_RUNTIME_DIR=/workspace/shared/ryoko-runtime scripts/run_tests.sh tests/agent/test_operations_checkpoint_recovery.py tests/hermes_cli/test_operations_profile_recovery.py tests/agent/test_operations_control.py tests/hermes_cli/test_operations_backup_extensions.py tests/hermes_state/test_runtime_store.py tests/hermes_state/test_context_projection.py tests/hermes_state/test_effect_records.py tests/hermes_state/test_delivery_outbox.py
Result: 8 focused files, 78 passed, 0 failed. All invocations used scripts/run_tests.sh; no raw pytest. Exact final log: /tmp/be14-recovery-final.log; SHA-256 4b40c593f7b10f8c8ab285f3529844375b68bf0fb9e9eec8c618090616c7931f. git diff --check passed for the owned scope. Source hashes, exact whitelist and detailed proof list: docs/build/be14-local-recovery-validation.json. Runbook: docs/build/be14-local-recovery.md.

Migration/flags: no schema migration, dependencies, runtime flags or updater frozen-surface changes. Existing schema versions/readers are checked exactly; incompatible code/schema is denied, never auto-downgraded. Only existing recorded event types are used.

Remaining qualification boundaries: local supported-owner/derived-projection recovery is implemented; this is not blanket full-profile/production disaster recovery. Multi-actor/legacy owning-store breadth and live cutover remain unqualified. Archive AES-GCM does not encrypt live databases/files/logs or plaintext staging. Production encryption deployment, key custody/loss strategy and secure staging/erasure need separate decisions. External providers/harnesses are never contacted or claimed restored. No live profile restore, secret handling, dependency install, deployment or external write occurred.

## 2026-10-03 BE13 completion — Configured local speech bridge

Before: media RPC always constructed VoiceIngress without adapters. Existing general transcription/Piper helpers could auto-download and backend TTS could target the wrong host.

After: existing served-profile local configuration selects a fixed offline faster-whisper/Piper worker. Explicit mono16k client PCM yields a reviewed transcript; explicit TTS returns exact bounded PCM bytes/digest to the same current client for playback. Capture cancel/speech stop invalidate only owned work, and transcripts enter missions only through unchanged confirmed normal command admission. Models are never loaded by a capability query. No schema migration, core tool, automatic fallback, installation, settings/secret change or backend playback.

Validation: final canonical six-file campaign passed 51/51 in 19.6 seconds from /workspace/shared/ryoko-backend-completion-validation, detached base 04c0319ce33f8c51e4b165c3728de5732b49cb5e plus only the nine frozen speech files. Every source hash matches the main workspace. Covers real configured RPC/factory/worker package-boundary fixtures, A→B→A isolation, exact bytes/digest, current-transport suppression after reconnect, missing/model-load/network failures, CPU/wall/output bounds, cancellation/reaping, strict policy-object and config budget rejection, existing budget/legacy voice and generated contract parity. Ruff and owned diff check pass. Earlier shared-worktree integration failures were caused by concurrent missing hermes_state_captures and stale unrelated generated contracts; the isolated canonical result supersedes that attempt without changing shared files. Exact receipts: docs/build/be13-speech-validation.json.

Canonical log: /tmp/be13-speech-isolated-tests.log, SHA256 e8a4bf9ab24ccab44ca316228b11e1c4feaab9ec311b86b32fa352546a8a8437

Implemented scope: non-budgeted configured local route with prerequisite-only readiness, strict no-download loading, CPU/wall/byte caps and same-client PCM return. Strict runtime_budget media remains an explicit code/certification gap, not a model-installation gate. Streaming STT, non-POSIX process bounds and live model/hardware qualification remain open. Frontend RPC/audio shape coordinated with the frontend worker.

Files are listed in /tmp/be13-speech-whitelist.txt. No staging, commit, root journal or shared schema edits by this worker.

# BE07 / FE02 / F04 capture completion

Implemented real explicit local text processing and bounded lexical/fuzzy retrieval on the canonical capture/artifact stores. Generated five typed registered RPC endpoints. Preserves original bytes/references, dates, annotations and every extraction attempt even when extraction is unsupported or bytes are unavailable. Search rechecks live exact project grants and namespace, rejects foreign transport/scope, and works with read-only grants.

Added schema44 additive index/consolidation/batch tables. Atomic reviewed SessionDB metadata batch commit checks exact digest, capture revisions, explicit selection, original-byte duplicate digests, source and destination grants; repeated exact commit returns retained receipt. Consolidation and filing are reversible metadata pointers with retained histories. No artifact movement or cross-ProjectsDB atomicity claim.

Final tests:30 passed,0 failed across5 files (mandatory scripts/run_tests.sh; explicit isolated interpreter/home/runtime). Includes schema43 reopen preservation, Unicode byte bounds, failed-extraction original download, stale index, malformed source/authority rejection, CAS rollback, duplicate history, grants and profile A→B→A. Generated freshness tests passed. Python syntax and scoped git diff --check passed.

Receipt: docs/build/be07-capture-completion-validation.json
Final log: /tmp/be07-capture-final-tests.log
Full command: HERMES_PYTHON=/workspace/scratch/42f2baf55663/ryoko-agent/.venv/bin/python HERMES_HOME=/workspace/shared/ryoko-dev-home HERMES_RUNTIME_DIR=/workspace/shared/ryoko-runtime scripts/run_tests.sh -j 3 tests/tui_gateway/test_capture_processing_rpc.py tests/hermes_cli/test_capture_processing.py tests/tui_gateway/test_project_sources_rpc.py tests/hermes_cli/test_project_sources.py tests/tui_gateway/contracts/test_generated.py

Exact file whitelist and SHA256 inventory are in the receipt. Backend contracts are frozen; parent owns shared registration/generated edits after04:33UTC. UI completion remains frontend-owned. No root journal/plan or Git writes performed.

## 2026-10-03 BE18 follow-up — Explicit disk-backed test scratch

The canonical runner assumed /var/tmp was disk-backed, while this host maps it to tmpfs and earlier fixtures exhausted space. Added optional --scratch-parent for a dedicated short per-user runner directory on explicitly chosen storage; default behavior remains unchanged. Tests prove per-file isolation/cleanup and preservation of unrelated parent contents. All20 runner tests pass, Ruff/whitespace clean. Receipt: docs/build/be18-runner-scratch-validation.json. Final campaigns will record disk-backed placement explicitly; no filesystem mount, network or security setting changed.

## 2026-10-03 BE11/BE12 completion — Bounded scheduled draft production

Before: durable review jobs inspected immutable sources/workflow pins but did not execute workflows or retain produced briefs.

After: paused workflow_draft schedules can execute the exact approved finite Markdown workflow only after an explicit bounded grant. Each fire pins authorized local source heads and actual parameters, uses the existing occurrence/claim/deadline/budget owner, creates a canonical mission/workflow run, and retains real immutable draft bytes through the existing effect journal. Source updates produce refreshed bytes. Grant/owner/policy/workflow/deadline checks occur at execution and result-storage boundaries. No second scheduler or fabricated human RPC context.

Human review: new runtime.schedule.output.get/prepare/publish contracts provide digest-checked inspection and a fresh ordinary approval/publish path over stored bytes. Review never reruns production, adopts an old generation or converts a production grant into publication approval. It publishes a new project artifact, not an automatic replacement head.

Safety: original deadline and one grant debit survive accepted-work restart; consumed approvals remain consumed; lost claims/fsync uncertainty remain unknown and non-replayable. Corrupt or unconfirmed outputs cannot be served. Paused/revoked/expired/changed-policy work cannot publish private results. No live schedule/config/deployment/connector/secret/Git actions were performed.

Validation: canonical runner,16 files,176 passed /0 failed in27.2s.17 new RPC/tick/storage tests +7 new pure contract cases; retained previous schedule/workflow/template/artifact/effect/notification/migration gates. Exact command/source hashes in docs/build/be12-workflow-production-validation.json; full whitelist including receipt hashes in /tmp/be12-workflow-production-whitelist.json. Generated contract drift passed and git diff --check is clean.

Migration/flag: no schema bump (schema44); no runtime flag; no creation or activation outside synthetic tests. Consumers were informed of frozen Python/generated DTOs before frontend implementation.

Remaining: local render_markdown slice only; scheduled domain/model/agent/script/remote execution, live setup, external delivery/publication and platform/client qualification remain unsupported or unqualified. Parent owns the phase/root journal/plan/Git checkpoint and final aggregate.

Backend roadmap status now links actual completion receipts and explicitly records unpublished local checkpoints, incomplete adapters and ungranted release gates. Final backend source will be frozen for consolidated verification; frontend integration remains independent.

## 2026-10-03 BE18 follow-up — Isolate synthetic refresh-lock fixture

The frozen final campaign exposed a credential-refresh test creating its lock under the real read-only home even though all credentials/endpoints were mocked. Point only the test at a temporary synthetic config directory. All3 cases now pass through the canonical runner; Ruff/whitespace pass. No production auth code, real credentials or frozen campaign source changed. Receipt: docs/build/be18-credential-fixture-validation.json.

## 2026-10-03 BE18 follow-up — Assert migration against current schema

The frozen final campaign found an old delegation migration test hardcoding target41 after additive schema44 work. It now asserts the migration reaches canonical SCHEMA_VERSION, retaining message preservation and added-table checks. All14 delegation cases pass through the isolated runner; production code is unchanged. Receipt: docs/build/be18-schema-fixture-validation.json. Frozen full-campaign failures remain recorded rather than rewritten.

## 2026-10-03 BE18 follow-up — Qualify declared optional dependency scopes

Built a separate PM-managed environment from the frozen lock with declared ACP/fal/aiohttp extras, without changing source/lockfiles or the interpreter used by the ongoing full campaign. The previously dependency-blocked selection now has360 passes,0 failures and1 retained skip across32 files. Source009255ec remains clean; no live account, provider call, credentials or runtime feature was configured. Exact packages, source/lock/log hashes and selection are in docs/build/be18-optional-extras-validation.json. This environment-qualified rerun supplements, rather than rewrites, earlier failed receipts.

## BE13 strict aggregate local speech budget adapter

Before: configured speech returned speech_budget_unsupported whenever runtime_budget was present. First-turn capture could not run under a strict tree.

After: runtime.voice.admit uses the real owned finite-control journal/lease/run-budget path, completes metadata-only admission, and returns the original account/root/deadline. It creates no active inference run or mission, and reuses existing current accounts. Strict capture/speak require stable request_id and budget_account_id; validated physical workers reserve and reconcile the existing aggregate tree. No reservation is created for empty, cancelled or refused capture. Known completion/cancellation charges actual physical attempts and wall; unknown termination retains durable units and concurrency; stale writers cannot refund; uncapped overruns create debt. Newer real submit accounts supersede old voice roots. Reconnect/retry never replays speech. Client playback remains separate.

Wire: new runtime.voice.admit with required request_id; optional-on-wire strict request_id and budget_account_id on capture.start/speak; feed unchanged. capability budget metadata declares explicit admission. Successful final speech carries the reservation receipt. Parent owns generated contracts and frontend integration.

Validation: 93/93 tests, 0 failed across 8 files via required scripts/run_tests.sh (-j2, configured isolated interpreter/home/runtime, --scratch-parent /workspace/shared), 28.9 seconds. Covers first-turn admission with/without existing mission, retry/new-click no renewal, expired/exhausted root, active/idle/newer run sharing, sibling/foreign refusal, real subprocess CPU/wall/cancellation, unknown/retired-fence retention, debt, no-work refusal, real SQLite reopen/no replay, and unchanged legacy routes. Ruff and scoped git diff --check passed. Static profile checker has 19 reviewed decorator advisories; full scoped handler/context and credential-free served-profile child environment are exercised.

Exact command, source hashes and log hashes: docs/build/be13-strict-speech-validation.json. Frozen whitelist/receipt hash: /tmp/be13-strict-speech-whitelist.json. Canonical log: /tmp/be13-strict-speech-final-verified.log. Shared agent/artifact_commands.py change owns only runtime.voice.admit; the sibling adds separate source RPCs.

No live setup/config/credentials, model calls/downloads/install, deployment, Git staging/commit, root plan/journal edits, other worktree or interpreter mutation. Remaining qualification: real model/audio/hardware quality and latency; streaming STT; Windows process limits. Unknown reservations remain explicit and require reconciliation; there is no new reset or replay API. Generated contract freshness/consumer checks remain parent-coordinated.

# BE10/BE12 connected read bridges

## Outcome
Implemented the finite Gmail-thread and Google Calendar free/busy adapters through the existing authenticated ConnectorClient. Both have real registered source prepare/publish RPCs. The frozen DTO is in tui_gateway/contracts/connected_sources.py. Central generator/server wiring belongs to the parent.

Final validation: 144/144 tests across8 files, including36 new bridge tests; targeted Ruff and git diff --check pass. Exact command, limits, hashes and primary documentation links are in docs/build/be10-connected-sources-validation.json. No live account calls or activation occurred.

## Authority and retained evidence
The request has no principal, policy, arbitrary tool name/arguments or source-body input. It selects one exact account plus mailbox/thread, or exact calendar IDs/window/timezone. The host validates the fixed read contract and returned schema without trusting readOnlyHint, descriptions, remote refs or regexes. Existing live identity/project grants and artifact-control ownership are checked at every guarded HTTP edge. Existing bearer peek supplies the profile credential without refresh or mint.

Originals retain exact selection, actor/project/policy, connector/tool, schema/argument/payload/recipient-endpoint digests, observed/fresh times and provider history where available. Calendar provider version is explicitly unavailable; content digest plus acquisition time identify the immutable observation. Original and typed projection are distinct canonical JSON artifacts, separately approved and derivative-linked. The original is canonical gateway JSON, not a claim to raw RFC822 or provider HTTP bytes.

prepare performs one schema request and one account-pinned read, once. publish consumes the exact retained proposals and never connects. Repeated prepare returns the same bounded per-agent bundle. Durable decision.observed audit prevents read replay after cache/process loss; user must start a new explicit refresh. A second-artifact failure returns partial with the first canonical ref and permits exact publication retry. Old versions remain readable.

## Budget semantics
Attempts mean physical requests to the selected connector gateway. They do not certify upstream vendor subrequests/retries. Cost mode fails closed because gateway pricing/upstream ceilings are not certified; token mode explicitly leaves spend untracked. Requests use no client retries, follow no redirects and do not inherit proxies. Timeout, malformed execute output and unconfirmed completion preserve unknown budget usage and a held remote slot. No exception silently releases a possibly active remote operation or retries it.

## Verified capability gap
Existing tools/connectors/gateway/wire.py records contract probe F2: account selectors are rejected with400 while gateway multi-account is off. This is a concrete gateway limitation, not absence of a user account. The adapter always includes account, returns source_pinned_request_rejected on400 and never drops the pin or substitutes pre/post active-account observations. No live qualification was attempted.

## Supported consumer flow
ConnectedSourceResult.record_json carries selection, coverage/freshness, original_ref/projection_ref after publication, research_request for immutable original bytes, and projection_research_request for decoded-text citation. Feed projection_ref into existing inbox.prepare or calendar.preview/agenda.plan contracts. Calendar participant fields contain explicit calendar IDs, not verified people. Inbox classifications and date mentions remain nonbinding proposals; attachments are metadata references only. No messages, calendar updates, invitations, or accepted obligations are created by the bridge.

## Validation coverage
Actual ConnectorClient and gateway wire with synthetic HTTP, real registered RPC and DTO checks, real SessionDB budgets/effects/immutable artifact catalog, exact original/projection retention, two kinds jointly researched with exact citation spans, schema drift, malicious JSON Schema refs/regex/recursion, authentication and grant revocation, explicit account rejection, A→B→A, wrong-thread discard, pending-loss/repeat behavior, old-version reopen, timeout/response bounds and uncertainty, cost and attempt denial, inert injection text, separate exact approvals and partial publication recovery. Existing gateway-client, wire, bridge, egress, research/brief and generated-contract tests passed.

## File ownership
- agent/artifact_commands.py  6acab9cd71102c0a50d205408e05f216edcf612245910910cac1aeabe798f454
- agent/connected_sources.py  0b28807671588d6bd1025f401a5a375d20d993f9bf3874e46cc19a5f92f04079
- hermes_cli/connected_sources.py  16ff5526d36d9f3e4dd170f28934c586a9dd1943ceab5a749d059b8a88a4e0e6
- tools/connectors/source_reads.py  82ca20847721eb0289c1dbfee2621503f8b1fecb22fe10b394d5773b37f0e997
- tools/connectors/gateway/client.py  4523723d50144ced66d59ed2526de47184e771bd729d55527cda7c1081f6b8b3
- tools/connectors/gateway/wire.py  ecc03f3c861ef5eb92adb14f16925fb0ae2083a43664765b6d8e3193f5f46686
- tools/egress_policy.py  dc3255d00adcb4032163ba676baaf7ba58e31198ab25e674c04992f26abe3dcb
- tui_gateway/contracts/connected_sources.py  654b9366c97ec1c70719b07866919cf742bf55c91343a09d718a0841aa22cfec
- tui_gateway/methods_connected_sources.py  b455e48913821e35b7c1ef0a11e7c49e9c7aecbc5a817379442491a5e03d07ce
- tests/tools/test_connected_source_reads.py  5cfff770b8d60652864c7d113dcb3c757bd3a656cba55655ad823c6b6dd1c8d8
- tests/tools/test_egress_policy.py  287823d0b01df8912423f98f6f608ad2224c3955aad61d8863306b4912604a0d

Receipt: docs/build/be10-connected-sources-validation.json
Log: /tmp/be10-connected-sources-tests.log

No Git staging/commits, root journal/build-plan edits, live deployment/config, installations, model/API calls or source text/secret attachments were introduced. Test content is synthetic only.

## 2026-10-03 BE14/FE11 completion — Owned repair and privacy controls

Added thin owned RPCs for existing runtime inspection, redacted audit, retention, checkpoint qualification, exact repair preview/apply and deletion preview/apply. The original maintenance broker retains all live policy, identity, target, digest, CAS, lease, journal and partial-deletion semantics. No client filesystem paths, credentials or arbitrary repair code; active model-run origins are denied. A revoked lease never claims remote work stopped. Logical deletion explicitly retains recovery/source/provider/backup limitations and cannot mutate the unknown primary harness.

Focused canonical checks:21 tests passed across2 files, including real owned RPC, wrong digest/transport/target, stale deletion manifest, no action on preview and truthful outcomes. Ruff passed. Receipt: docs/build/be14-operator-rpc-validation.json. Generated integration and real typed frontend acceptance follow in the coordinated checkpoint. No live user data, settings or credentials were touched.

Coherent integration: the exact30-file slice was copied to an isolated checkout at18071c8c and verified through the canonical runner:218 passed,0 failed across15 files in63.9s, including generated contract parity. Staged source was byte-verified against that isolated tree; later specialist/opportunity work remains separate. Exact hashes: docs/build/backend-adapters-integration-validation.json. No remote publication, live account access or activation is claimed.

## FE13 — Consolidate owned frontend workflows and offline acceptance

Status: verifying; local implementation/evidence checkpoint, not release sign-off. Existing Hermes TUI, web-hosted TUI and Electron surfaces remain authoritative consumers of backend contracts. Added typed artifact branch/template flows, supplied-output influence review, capture search/processing/batch review, scheduled drafts, bounded local monitor controls/receipts, agenda/decline and explicit configured local-backend microphone/playback controls. Added nine-locale chrome and compiler/StrictMode-safe scope guards; same-object reconnect invalidates approval state immediately while bounded passive original IDs remain inspectable.

Validation: full desktop renderer 1,204 files/10,635 tests; shared 36/277; offline TUI 179/1,618; web 50/359, all passed. All declared frontend types and final scoped/full lints passed; inherited warnings retained. TUI/web/pure desktop renderer builds passed. Full desktop renderer aggregate preceded an erasableSyntaxOnly constructor compatibility correction; affected 15 shared +17 compiled UI tests passed separately, plus clean 7-file/23-test compatibility rerun. Exact commands, hashes and original failed attempts are retained in docs/build/fe13-validation.json.

Native/helper aggregate: 339 passed, 7 failed, 7 skipped files; 3,261 passed, 6 failed, 15 skipped tests. All failed test files were unchanged from candidate base. A prepared-Python rerun resolved four helper files (6/6 tests). Remaining Electron collection and Unix-socket permission limits are explicit. Packaged test was attempted: the skip-build flag did not skip a missing app, native rebuild failed before any package was produced, and was not retried. Pure renderer success is not native launch evidence.

The initial TUI aggregate was blocked by an unanticipated geolocation request from an existing fixture. No live retry or network workaround occurred; a rejecting fixture-only mock proved the route offline, production weather unchanged, then the entire TUI suite passed. Cloud Browser separately blocked the loopback visual fixture under URL policy; zero screenshots, keyboard/narrow-window visual acceptance pending. Native/hardware/provider/model and human-effort pilot gates remain open.

Required additive consumers for newly committed/frozen backend APIs remain separate follow-up work: operator repair/privacy, strict speech admission/accounting, exact connected-source snapshots, named configured specialists and bounded on-demand opportunities. Full teams/unsolicited activation and LAYA modes remain disabled/value-gated. No Dots repo/cutover, credentials, deployment or publication claim. Normal Git authentication remains unavailable; this checkpoint is local only.

# BE13 named configured specialist controls

Date: 2026-10-03 UTC

## Scope and behavior

Closed the FE10 configured-specialist selection/handoff gap. The existing roster
and steer/interrupt controls remain separate. New typed catalog, preview, handoff
and status methods inspect configured leaf specialists and execute one actual
local child through the canonical submit command, admission queue, lease, budget,
mission and existing delegation machinery.

No RuntimeRun or human RPC context is fabricated. No database schema was changed
by this slice. No model prompt merely requests use of a named specialist. No
provider, credential, runtime setting or installed integration was edited. Teams
remain disabled pending their measured gate.

The narrow, approved lifecycle extensions are in runtime_commands.py (internal
submit payload validation), turn_facade.py (execution under its genuine claim),
and mission_runtime.py (durable parent review without automatic continuation or
mission acceptance). The public generic runtime.command input remains narrow.

Selections preserve exact configured role/manifest/methods/output schema, expiry,
explicit project, source/evidence references, parent policy and mission witness.
Parent and child ACLs are checked live. Built-in memory remains individually
owned and persists for the same stable named specialist across child sessions.
The existing root budget, executor and completion/delivery machinery is reused.

Command retries/reconnect return the original command. Claimed uncertain work is
inspectable without adoption or relaunch. A host cancellation remains cancelled
even if child output validation also fails; iteration-truncated child output is
retained as partial evidence rather than labeled complete.

## Validation

Only synthetic on-disk profiles and guarded SDK HTTP fixtures were used. Real
agents, SDK serialization, immutable artifact publication, child construction,
individual memory, capability dispatch, hierarchical budgets, mission verification,
RPC ownership, admission, result persistence and delivery were exercised.

The exact final command, tested source SHA-256 values, test counts and log hashes
are recorded in docs/build/be13-named-specialists-validation.json. The operational
notes are docs/build/be13-named-specialists.md.

Final qualified campaign: 315 tests passed, 0 failed, across 24 files in 90.3s
using the mandated isolated runner and interpreter with -j2. Scoped Ruff and
Git diff checks passed. Generated TypeScript/OpenRPC parity passed. The final
log is /tmp/be13-named-specialists-qualified.log. Observed validation base HEAD:
6316814532a5df2d89568880a51f619bbf421e78.

The work includes negative tests for altered/expired selections, injected grant
fields, generic RPC bypass attempts, revoked child project ACLs, missing exact
method bytes, owned transport changes, queued and running cancellation, unknown
claims, schema failure and partial iteration exhaustion. The parent's frozen
prompt remains unchanged. Snapshot and replay remain representable by the
declared wire contracts.

Intermediate failures were investigated: an immutable artifact correctly refused
a fixture overwrite, which was replaced by a missing-bytes test; client rebuilds
within one child session were de-duplicated by session identity in the memory
assertion. A real cancellation projection issue was fixed and retested. A shared
opportunity-method factory name collision was reported to its owner and fixed
there; the final campaign includes its corrected registration.

## Boundaries and integration

This is local leaf specialist support. Live provider/integration/hardware
qualification, remote executors, resumed Python execution, and team benefit
measurement are not claimed. Parent review remains explicit. New methods heads
do not replace a configured immutable methods version silently.

No Git/index changes or root plan/buildjournal edits were made by this worker.
Shared contract aggregator/server/generated changes were coordinated with the
backend parent and opportunity worker after the prior staged slice was frozen.
Receipt hashes of shared files may include the opportunity slice and are
identified separately from this slice's topical ownership.

# FE11 opportunity review implementation checkpoint

Implemented explicit selected-project, on-demand candidate review with three honest local rules: current stale artifact head, accepted waiting commitment whose explicit due/check time is reached, and latest unevaluated workflow draft. Added four frozen typed RPCs and generated TS/OpenRPC contracts. All dispositions are metadata only: no command/mission/task/commitment/effect/approval dispatch or creation.

Canonical storage is additive SessionDB schema45, with CAS/idempotency, bounded candidate/history/request retention, unchanged dismissal suppression (including replayed scan requests), semantic-source deduplication, changed-source explanations and fresh atomic acceptance guards. Source reads remain selected-project and live-grant fenced. Existing store bundle validation includes the new metadata and referenced projects.

Focused real registered RPC + bundle/restore + generated parity validation passed 9 tests across 3 files. This includes schema44→45 preserving effects/approvals, restart suppression, selected A ignoring corrupt excluded B, A→B→A, grant revocation, bounds, current evidence, concurrent one-winner CAS and duplicate-request deduplication. Wider adjacent validation passed 63 tests across 16 files with zero failures or retries (42.2 seconds). The exact command, source whitelist and SHA256 values are in docs/build/be11-opportunity-validation.json. git diff --check passed.

The initial focused run exposed a gateway helper collision: _handler was renamed _opportunity_handler. A later fixture-only artifact reference contained an unsupported sha256 key; corrected to the existing artifact/version wire shape. Both were rerun green. No Git staging, commits, root journal or buildplan edits performed by this worker.

Operator integration: prefixed helper bindings to avoid gateway shared namespace collisions; actual RPC 2/2 passed. Combined source receipt is docs/build/backend-specialist-opportunity-integration.json. Local only; final consolidated campaign remains pending.

# BE10/BE12 connected read bridges

## Outcome
Implemented the finite Gmail-thread and Google Calendar free/busy adapters through the existing authenticated ConnectorClient. Both have real registered source prepare/publish RPCs. The frozen DTO is in tui_gateway/contracts/connected_sources.py. Central generator/server wiring belongs to the parent.

Final validation: 144/144 tests across8 files, including36 new bridge tests; targeted Ruff and git diff --check pass. Exact command, limits, hashes and primary documentation links are in docs/build/be10-connected-sources-validation.json. No live account calls or activation occurred.

## Authority and retained evidence
The request has no principal, policy, arbitrary tool name/arguments or source-body input. It selects one exact account plus mailbox/thread, or exact calendar IDs/window/timezone. The host validates the fixed read contract and returned schema without trusting readOnlyHint, descriptions, remote refs or regexes. Existing live identity/project grants and artifact-control ownership are checked at every guarded HTTP edge. Existing bearer peek supplies the profile credential without refresh or mint.

Originals retain exact selection, actor/project/policy, connector/tool, schema/argument/payload/recipient-endpoint digests, observed/fresh times and provider history where available. Calendar provider version is explicitly unavailable; content digest plus acquisition time identify the immutable observation. Original and typed projection are distinct canonical JSON artifacts, separately approved and derivative-linked. The original is canonical gateway JSON, not a claim to raw RFC822 or provider HTTP bytes.

prepare performs one schema request and one account-pinned read, once. publish consumes the exact retained proposals and never connects. Repeated prepare returns the same bounded per-agent bundle. Durable decision.observed audit prevents read replay after cache/process loss; user must start a new explicit refresh. A second-artifact failure returns partial with the first canonical ref and permits exact publication retry. Old versions remain readable.

## Budget semantics
Attempts mean physical requests to the selected connector gateway. They do not certify upstream vendor subrequests/retries. Cost mode fails closed because gateway pricing/upstream ceilings are not certified; token mode explicitly leaves spend untracked. Requests use no client retries, follow no redirects and do not inherit proxies. Timeout, malformed execute output and unconfirmed completion preserve unknown budget usage and a held remote slot. No exception silently releases a possibly active remote operation or retries it.

## Verified capability gap
Existing tools/connectors/gateway/wire.py records contract probe F2: account selectors are rejected with400 while gateway multi-account is off. This is a concrete gateway limitation, not absence of a user account. The adapter always includes account, returns source_pinned_request_rejected on400 and never drops the pin or substitutes pre/post active-account observations. No live qualification was attempted.

## Supported consumer flow
ConnectedSourceResult.record_json carries selection, coverage/freshness, original_ref/projection_ref after publication, research_request for immutable original bytes, and projection_research_request for decoded-text citation. Feed projection_ref into existing inbox.prepare or calendar.preview/agenda.plan contracts. Calendar participant fields contain explicit calendar IDs, not verified people. Inbox classifications and date mentions remain nonbinding proposals; attachments are metadata references only. No messages, calendar updates, invitations, or accepted obligations are created by the bridge.

## Validation coverage
Actual ConnectorClient and gateway wire with synthetic HTTP, real registered RPC and DTO checks, real SessionDB budgets/effects/immutable artifact catalog, exact original/projection retention, two kinds jointly researched with exact citation spans, schema drift, malicious JSON Schema refs/regex/recursion, authentication and grant revocation, explicit account rejection, A→B→A, wrong-thread discard, pending-loss/repeat behavior, old-version reopen, timeout/response bounds and uncertainty, cost and attempt denial, inert injection text, separate exact approvals and partial publication recovery. Existing gateway-client, wire, bridge, egress, research/brief and generated-contract tests passed.

## File ownership
- agent/artifact_commands.py  6acab9cd71102c0a50d205408e05f216edcf612245910910cac1aeabe798f454
- agent/connected_sources.py  0b28807671588d6bd1025f401a5a375d20d993f9bf3874e46cc19a5f92f04079
- hermes_cli/connected_sources.py  16ff5526d36d9f3e4dd170f28934c586a9dd1943ceab5a749d059b8a88a4e0e6
- tools/connectors/source_reads.py  82ca20847721eb0289c1dbfee2621503f8b1fecb22fe10b394d5773b37f0e997
- tools/connectors/gateway/client.py  4523723d50144ced66d59ed2526de47184e771bd729d55527cda7c1081f6b8b3
- tools/connectors/gateway/wire.py  ecc03f3c861ef5eb92adb14f16925fb0ae2083a43664765b6d8e3193f5f46686
- tools/egress_policy.py  dc3255d00adcb4032163ba676baaf7ba58e31198ab25e674c04992f26abe3dcb
- tui_gateway/contracts/connected_sources.py  654b9366c97ec1c70719b07866919cf742bf55c91343a09d718a0841aa22cfec
- tui_gateway/methods_connected_sources.py  b455e48913821e35b7c1ef0a11e7c49e9c7aecbc5a817379442491a5e03d07ce
- tests/tools/test_connected_source_reads.py  5cfff770b8d60652864c7d113dcb3c757bd3a656cba55655ad823c6b6dd1c8d8
- tests/tools/test_egress_policy.py  287823d0b01df8912423f98f6f608ad2224c3955aad61d8863306b4912604a0d

Receipt: docs/build/be10-connected-sources-validation.json
Log: /tmp/be10-connected-sources-tests.log

No Git staging/commits, root journal/build-plan edits, live deployment/config, installations, model/API calls or source text/secret attachments were introduced. Test content is synthetic only.

## Prepared-source review seam follow-up

Added runtime.sources.preview after frontend correctly found that unpublished proposal bytes were not readable. It serves bounded exact original/projection chunks under the original live session/lease/proposal and durable approval binding, with full digest/size and approval IDs. No external source call or publication occurs. Source contracts and generated files are frozen and shared with the frontend lead.

Final focused validation:58/58 across source bridge (46), generated parity (2), and artifact RPC regressions (10). Complete assembly agrees with proposal digest/size and later publication. Cross-session RPC, tampered cache, cancelled/completed control, replaced lease, expired bundle, denied approval, revoked project and bad bounds all fail without exposing bytes. Ruff and scoped diff checks pass.

Receipt: docs/build/be10-source-preview-validation.json
Log: /tmp/be10-source-preview-tests.log

Follow-up exact code/generated whitelist and hashes:
- agent/artifact_commands.py  31ba74002f63acf35c88dbb958a7e78982999152707f675416c2ac2759c98920
- hermes_cli/connected_sources.py  9a4dab2a692b3f70b50247df4453702eaf89a64b2725880f3511c3d57870fbb9
- tui_gateway/contracts/connected_sources.py  4a505a4d2361e430fee5bbcff4daf5551b30a86f0743cea3c59af9f758f3294b
- tui_gateway/methods_connected_sources.py  5b50eca640acd04a571987ef8d68c41ba3d2576bd383f9fddfa114cadb41a7fb
- tests/tools/test_connected_source_reads.py  22e514128c578fa981a3c0c3495a0e94ff761c70cdee8e59f206baaec2bbdfc5
- apps/shared/src/gateway-contract.generated.ts  73f089aeca65cbc1e90c8a54da0f7d11c144fc160e8d8cc99e28043e9eef1839
- apps/shared/src/gateway-contract.openrpc.json  1d3151320c621de5b7032a4e5fb4ba170fe09fa5400fd762df832bca09622909

## BE18 — Preserve second full campaign and bounded follow-up evidence

The frozen 009255ec campaign completed with 55,930 passed, 444 failed and 730 skipped across 5,348 files. docs/build/be18-second-campaign-validation.json preserves exact log hashes and distinguishes reporter phases, baseline node comparison, prepared-dependency checks and subsequent source. Combined 2244f515 actual specialist/opportunity/operator/contracts passed 35/35. Default-scratch Docker host fixture passed 28/28; three inherited project-root fixture assertions remain affected by protected host .git markers, unchanged after default-scratch rerun. No host marker was removed. Later source needs the next frozen-source campaign; no release signoff or remote publication is claimed.

## FE13 follow-up — Finish required additive runtime consumers

Status: verifying. Closed the five remaining feasible local consumer seams against backend candidate b323e73f: exact operator repair/privacy review; strict finite speech admission and accounting; account-pinned connected source prepare/full-byte review/publish; named configured single-specialist handoff; bounded explicit opportunity review. Each uses existing runtime authority and existing Electron surfaces; no second memory, scheduler or agent loop.

Connected source review now uses the real cached preparation preview RPC, complete bounded chunks and full SHA validation before independent original/projection approval. It never publishes from a metadata-only digest view or refetches at publish. Specialist handoff pins real configured identity/methods/policy/mission references and leaves parent review explicit. Opportunity acceptance only records choice and opens existing review controls. Operator logical deletion acknowledges individual stores and retains its partial-erasure limits.

Strict speech admission starts no microphone/model/mission; recording and synthesis remain separate explicit actions with original request/account IDs. Lost media usage stays unknown through disconnect or capture discard; budget inspection does not invent a receipt or reset an account. Scope/reconnect fences and passive recovery identifiers cover all new operations; copy follows all nine existing locales.

Clean combined gates: full shared 41 files/368 tests, all runtime/voice UI 32 files/250 tests and TUI runtime 4 files/8 tests passed. Shared/TUI types/lint, declared desktop type/lint and web/pure renderer builds are individually recorded in docs/build/fe13-followup-validation.json. The original full UI/TUI aggregates remain separately source-bound; overlapping counts are not summed. Initial default-heap and concurrent-resource type failures plus web fingerprint retry remain visible in the receipt. No native rebuild, live provider/model/hardware or blocked browser retry occurred.

Remaining: exact live/native/visual and human-pilot gates, backend final full campaign, external personal-harness operations, conditional full teams/LAYA and separate Dots consumer qualification. These are not waived by local test counts. Normal Git publication remains authentication-blocked; checkpoint is local only.

## FE14 — Record producer-side Dots adapter handoff

Status: partial, producer handoff only. Added frontend-adapter-handoff.md with exact current producer contract inputs, OD00 read-only identity/snapshot/replay proof, one-runtime/one-scheduler/artifact-owner invariants, isolated specialist memory, exact approval/delivery semantics, canary prerequisites and rollback ownership. No source in the separate Dots repository was changed, no consumer commit exists, and OD00/OD01–OD04 are not claimed complete.

Validation is documentation/contract-boundary only: exact generated TypeScript/OpenRPC bytes match producer b323e73f; OpenRPC parses with 407 declared methods, required runtime control/read methods exist, producer-only synthetic fixture identity is retained, and local handoff links resolve. Exact hashes and limits are in docs/build/fe14-validation.json; there is no visual, live, deployed or cross-consumer acceptance claim.

FE00–FE13 local implementation checkpoints remain individually verifying with their receipts. FE14's separate-consumer implementation/readiness gate remains pending; this phase does not authorize cutover, credentials, deployment, model training or LAYA activation. Normal Git publication remains unavailable; documentation checkpoint is local only.

## BE18 — Qualify newly collected API transport fixtures

The prepared optional-dependency environment exposed transport tests previously blocked at aiohttp collection. Unspecced mock agents fabricated identity and memory-manager state; strict checkin correctly rejected them. Changed only 35 mock constructors in six test files to declare the absent legacy attributes. No production ownership policy changed. All six files and three existing real-agent/strict-ownership suites passed: 260 tests across nine files, no retries. Exact hashes and original failure are in docs/build/be18-api-memory-fixture-validation.json. The running b323e73f full campaign remains frozen and its original failures are retained; this is a separate affected-suite qualification.

## BE18 — Isolate doctor configuration fixture from host installation

The prepared runtime exposed an unrelated command-install repair invoked by a configuration-only test helper. Scoped that helper to the three existing real Configuration Files checks; production logic and filesystem protections are unchanged, and no host command was installed. The complete doctor file plus command-install coverage passed 73 tests, with eight macOS-only skips and no retries. See docs/build/be18-doctor-fixture-validation.json. The running immutable full campaign retains its original outcome separately.

## BE18 — Qualify the remaining hosted-room memory fixture

The final newly collected two-gateway transport fixture had the same unspecced mock-agent ownership mismatch. One constructor now declares absent legacy memory/context attributes; no production policy changed. Hosted-room and real ownership/reuse suites passed 16 tests. Exact source and log hashes are in docs/build/be18-hosted-room-fixture-validation.json. Original full-campaign failure remains recorded separately.

## BE18 — Record final frozen-source campaign and local handoff

Status: implementation checkpoint complete locally within the selected scope; release blocked. The clean immutable backend candidate b323e73f7b2f6792c57341240e206a72e43e3192 completed the canonical four-worker campaign in 3,501.2 seconds: 5,356 files, 56,979 passed, 219 failed and 724 skipped. The runner also reports 12 files with passing tests but nonzero exit and 15 files where no tests ran. The complete sanitized per-file index preserves 484 failing node IDs and phase outcomes separately from the runner summary; 455 node IDs match the preceding campaign, which is comparison evidence rather than proof of identical causes.

Twenty-seven new nodes became executable after optional dependency setup and were transport-mock ownership mismatches; the six API and one hosted-room fixture corrections passed 260 and 16 tests separately. A newly exposed doctor fixture invoked unrelated host installation; its scoped correction passed 73 tests with eight platform skips. The unchanged LSP descendant cleanup test hit the live-system signal guard and remains unqualified; no guard bypass or direct signal retry occurred. The original full-run counts were not rewritten.

The final campaign's selected 29-file authority/effect/memory group passed 357 tests with no skips. Explicit final release slices/tooling/generated parity passed 14 tests. Ruff passed all 196 changed Python files since the published BE14 checkpoint. Frontend FE13 and additive consumer receipts retain their own full/affected aggregates, types, lint and pure builds; FE14 is a producer-side handoff only. The current manifest and current-scope document distinguish implemented finite local paths from unsupported/conditional/live/native/visual/harness/Dots gates. No model training, LAYA activation, deployment or credential use occurred.

Exact evidence: docs/build/be18-final-source-validation.json, docs/build/be18-final-test-index.json, docs/build/release-manifest.json and docs/build/backend-current-scope.md. Full test failure blocks release signoff despite focused successes. Normal Git authentication remains unavailable: remote main was last exactly verified through BE14, and all later phase commits remain local-only with a refreshed recovery bundle. No API-blob publication or CI-green claim is made.

## BE18 — Repair and classify remaining runtime fixture failures

Continued failure recovery after the initial final-source receipt. All 36 assigned runtime/gateway/TUI/cron/monitoring nonzero modules now have per-node evidence and independent baseline relationships. Of 84 original failing node outcomes, 66 are qualified fixture repairs (39 in this slice and 27 previously repaired); 18 remain blocked by AF_UNIX restrictions or the untouched LSP live-system guard. Three additional optional-dependency collection skips are not failing test nodes. Synthetic negative ancestry fixtures explicitly exclude unrelated enclosing host markers; they do not prove a real outside-checkout host.

The exact final 17-file patch is fixture-only, including the synthetic API probe; production runtime is unchanged. Canonical qualification passed 883 tests across 16 files, with final hashes and per-file receipts verified. Ruff and diff checks passed. See docs/build/be18-runtime-fixture-repairs.json and docs/build/be18-runtime-failure-classification.json. The original full-suite failure receipt remains intact; no security/process/filesystem guard was bypassed.

## BE18 — Recover package and script fixture failures

Qualified 162 of 166 original failed node outcomes across 24 PM/script modules. Existing hash-pinned runtime wheels, the existing IDNA fixture pin and original uv bootstrap archive were obtained through the normal official downloader, verified, and consumed only in isolated fixtures. Opt-in pytest cache/wheelhouse settings preserve defaults, copy rather than mutate shared caches, and retain real build/install/cold-start/hash-failure paths. Dependency-free local fixture build backends avoid implicit setuptools fetches. No production code or package versions changed.

Consolidated original-module outcomes: 240 passed, four GPG failures, three skips; the new cache-isolation guard tests passed three cases. A short owned-home diagnostic proves GPG agent socket creation is denied with EPERM; no user keys, guard bypass or broad process kill. The legacy cold-bootstrap row remains skipped because Python 3.11 is absent. Exact source/log hashes and all node dispositions are in docs/build/be18-package-source-manifest.json and docs/build/be18-package-failure-classification.json. Failed attempts and the original full campaign are retained.

## BE18 — Recover and classify CLI failures

All 150 original CLI failed node outcomes are classified: 125 now qualified, one doctor fixture previously qualified, and 24 host-blocked (17 socket EPERM and seven profile-export cases under protected Git ancestry). The affected distinct modules passed 204 tests with four Windows skips; the five owned fixture files passed 39 tests with four skips using the actual receipt-plugin argv.

Fixed only fixture facade targeting, fake opener signature, inherited test argv, canonical alias expectation and synthetic service UID/GID. Real chown/chmod/FIFO assertions and dedicated security tests remain intact; distinct-UID live-container ownership is not certified. Shared offline PM fixtures recover previously blocked plugin setup without changing source authority. No CLI production edit, export guard bypass or alternate writable-root workaround occurred. Evidence: docs/build/be18-cli-failure-classification.json.

## BE18 — Recover tool failures and two pre-existing production defects

Qualified 21 of 84 original failing tool/plugin/verify outcomes; 63 remain blocked by socket EPERM. Focused qualification passed 417 tests across 15 files, including the unchanged Docker fixture under the canonical default scratch location. Optional-dependency skips, non-host skips and deliberate deselection are separate from failures.

Two production defects, responsible for three failing outcomes, are repaired: Parallel now uses its canonical key-first cached SDK acquisition rather than a redundant eager install; MCP child enumeration reaches its existing current-parent psutil fallback only when the procfs children interface is absent. Readable-empty, malformed/unreadable and PermissionError semantics remain fail-closed; process signal/ownership guards are unchanged. Real cross-thread-child, missing-key/no-acquisition, keyed-SDK error and adjacent lifecycle/SSRF tests pass. Other edits isolate fixture paths, sudo discovery, synthetic deletion targets and fake DNS while preserving real policy checks. Exact hashes, baseline comparisons and dispositions: docs/build/be18-tools-failure-classification.json.

## BE18 — Correct the final LSP teardown classification

Deeper full-trace inspection showed the LSP test body had already verified successful cleanup, but finally attempted to signal a reparented zombie because is_running includes zombies. Corrected only the fixture teardown predicate and NoSuchProcess race handling. Fresh canonical real-child validation passed all 13 tests; the live-system signal guard is byte-unchanged and no original PID was signaled. The former host-blocker assessment is preserved as history and superseded by fixture-isolation recovery. Runtime classification is now 67 recovered and 17 genuine host-blocked original outcomes. Evidence: docs/build/be18-lsp-zombie-fixture-validation.json and updated runtime classification.

## BE18 — Final observed-failure reconciliation and supported-host handoff

All 117 original nonzero-exit module receipts and all 484 original failing node outcomes are accounted for exactly once. Qualified recoveries total 376; 108 remain demonstrated host constraints (101 socket-EPERM outcomes and seven profile-export checkout-ancestry outcomes). Unclassified observed failures: zero. Unfixed observed production regressions: zero; two pre-existing production defects and one stale test expectation were repaired. This says nothing about latent defects or unexercised host/live capabilities. Every one of the 29 failure nodes newly visible relative to the preceding campaign has now qualified.

The final combined repair integration passed 182 tests across six modules; the subsequent LSP fixture-only delta passed 13. Ruff passed all 48 changed Python files. Focused lane receipts retain exact source/log hashes, source comparisons, failed attempts, skips and overlapping counts. The original full campaign remains 56,979 passed, 219 failed and 724 skipped; phase-aware recovery counts are not subtracted from that different summary. No fourth full run was performed merely to repeat proven host denials.

Current evidence: docs/build/be18-failure-resolution.json, four detailed lane classifications, docs/build/remaining-host-tests.txt and the updated release manifest. A suitable already-authorized host and outside-checkout temp root are needed for remaining integration proof. No host security settings were changed, no export/signal/transport guard was bypassed, and no live user credentials, model training, LAYA activation or deployment were used. The temporary Vite fixture server was already stopped normally; no browser-policy retry was made. Main publication remains blocked on normal Git authentication; all post-BE14 work is local with its recovery bundle.

## Dots prerequisite — Canonical owner-scoped conversations and read-only command recovery

Added a versioned generated `runtime.conversation.*` boundary for the isolated server-owned single-owner stdio deployment, plus `runtime.command.receipt`. Conversation capabilities return the trusted principal/profile/active-agent and configuration/policy digests without profile paths or secrets. Caller-selected authority and WebSocket use are rejected. Schema46 stores canonical compression-root ownership and durable request-digest create/rename/archive receipts; `operation.get` inspects unknown outcomes without creating or queuing work. Legacy generic session attachment remains unchanged and is not treated as ownership enforcement.

Create is atomic and restart-idempotent; rename/archive use metadata revision CAS and durable intent-conflict checks. Bind authorizes the stored identity before initializing/reusing a live record and reports building/ready/failed honestly. Owner-only title search/list uses bounded stable-key pagination. History/export expose committed safe user/assistant text, stable message UIDs and validated compression lineage, with UTF-8-safe 16,384-byte chunks and a 262,144-byte page text ceiling. Existing command receipts are projected with status/revision in one read snapshot; inspection cannot enqueue, claim, or execute.

Verification: 56 tests passed across nine focused/regression modules, and 20 passed across three disjoint additive-migration/identity/lineage modules. The real isolated stdio subprocess fixture passed abrupt restart and read-only receipt recovery without agent/provider activation. Generated TS/OpenRPC freshness, shared TypeScript typecheck, Ruff on eleven changed/new Python implementation/test files, and diff checks passed. Atomic injected receipt failure leaves no partial session, and schema migration preserves existing transcript/runtime/effect rows without adopting legacy ownership. See `docs/build/dots-conversation-prerequisites.md` for exact contract and qualification boundaries.

Remaining explicit limits: command-to-message linkage is unavailable pending transcript-writer linkage; safe export is not import/restore or private runtime backup; append-watermark paging is not an immutable edit/rewind snapshot; summary-carrier-only human payload parity is unqualified; mission storage still has one mission per durable runtime session. No browser/BFF authorization, live provider/credentials, multi-human WebSocket delegation, deployment, or full-suite qualification is claimed. Source edits only; publication remains with the integrating parent task.

## Dots prerequisite repair — Canonical display titles

Fixed two defects observed through the actual Dots Node-to-Python consumer: duplicate default conversation titles and renaming a compressed conversation both conflicted with the legacy globally unique session alias. Canonical display titles now live in their own metadata column, are backfilled for existing owned conversations, and support duplicate/blank display titles without changing physical session alias semantics for legacy callers. Canonical rename remains revision-checked and receipt-idempotent; it does not assign one unique legacy alias to every physical compression row.

Source prerequisite baseline: published `2322ee12cceaa376e6eed9a93b4bbf4018c51d53`. Standalone reviewed local unit `2e42dac5ce4138049256756a0308302dd8747f79` is preserved. Twelve tests across five focused files passed, covering duplicate/default titles, compression rename/search/pagination/restart, retained mutation receipts, legacy backfill, ownership isolation and schema probes; compileall and diff checks passed. Generated TypeScript/OpenRPC bytes are unchanged. Dots BE02 repeats its previously failing real consumer title cases against the published repair before acceptance. No selector/config feature, deployment, credential or provider activation is bundled in this checkpoint.

## Dots integration prerequisite — Durable execution and transcript recovery

Integrated the approved Dots producer interfaces against published baseline `44eec9a9650414aef3e95ef6bf78eebedbb92265`. Canonical command receipt reads now authorize the recorded durable owner without constructing or binding an agent, including archived conversations and provider outages. Transcript writes atomically record command-to-message UID links; accepted input remains distinct from committed history. Cancel and steer optionally name an exact target run with revision CAS, never signal a successor or replay a repeated control. Only canonical authenticated stdio bindings use the existing local TUI execution surface; durable web provenance and external web/gateway denial remain unchanged. The latter defect was reproduced on the combined base before its one-line fix; actual agent construction, mock-SDK execution and receipt-only repeat now pass.

## Dots integration prerequisite — Review, global control and sequential missions

Added immutable digest-bound safe approval detail, persisted owner/profile pause/resume with idempotent operation receipts and admission/claim/effect fences, plus explicit mission replacement and immutable prior mission history. Sensitive or legacy unreviewable approvals fail closed. A pause retains admitted work and cannot roll back a completed external action. Mission mutation after replacement requires its exact mission ID and revision, preserving old effect, approval and budget audit rows.

## Dots integration prerequisite — Scheduled commands and frozen owners

Schedules now admit durable commands through the existing queue and sole agent loop, with bounded launch lifetime, overlap/missed-run policy, expiry and budgets. Legacy imports remain paused pending explicit old-owner retirement and unresolved-occurrence reconciliation; producer receipt identifies foreign cutover as unverified rather than pretending to inspect the old scheduler. Managed/copy-agent scheduling and notification routing resolve frozen recorded bindings, and revocation is rechecked inside admission. Regrant cannot revive old session authority. Real copied-agent/mock-SDK tests cover restart, owner changes and revoked admission without debit.

## Dots integration prerequisite — Page/computer effects and optional native tools

Added trusted-stdio adapter registration, exact page/computer proposals, digest-bound approval and effect dispatch/inspect contracts. Page byte/CAS receipts remain the BFF store's authority; computer effects require current grants, control revision and fresh snapshot identity. Unknown outcomes reconcile read-only and never authorize blind replay. Optional `dots_native` tools are enrolled only for future sessions after explicit client capability, not added to core defaults. Typed native approval callbacks preserve one existing decision owner. Actual agent-loop tests use simulated provider responses and broker callbacks; no real computer supervisor, credentials or external effects are qualified by these tests.

## Dots integration prerequisite — Managed specialists and reviewed skill delivery

Added durable managed-agent configuration with immutable primary/specialist memory roles, isolated specialist namespaces, desired versus enrolled revision reporting, and monotonic revocation. Narrowing/archive takes effect for old authorities immediately; expansions enroll only in a new session. Reviewed, version-pinned skill delivery and rollback store real installation records. Personal memory harness mutation remains unavailable where the harness has no such protocol; no primary-memory copy is created for specialists.

## Dots integration prerequisite — Combined verification and limits

Preserved original local units: `64698d467d8874f2e08020a3396b9118f0edaf83`, `cfbfdf3a8ea2107038c194a71b7fe2b0b6e01277`, `a337589d445b9468883668c50dbf1af00b062901`, `8d124eb137f3e78319d948ccc0f8c9f948a8451e`, `962e587bf24f713b119fe0b469a886cfa0a055b8`, `8b36e8b1e6dd964a759631d0270a21f1dbf7b24b`, `e2d5c5edb742ea42554963da19dcbcb426ab7e42`, `27aba41ff946abcb9a12ca9403c434973080227d`, `d5435abf`, `3eebaf8405472b1c1bb5962a287293ac9f259151`, `6eda3bef319aa3be2811e1c4428216e3c8b7b4b7`. Published title repair is retained unchanged.

Bounded combined core gate: 116 tests across 19 files passed. Final receipt/schedule delta: 43 tests across five files passed; exact-target/generated-contract gate: 19 tests across two files passed. Final canonical execution surface gate: 39 tests across six files passed, with baseline-red regression. These overlapping runs are separate evidence, not an additive unique-test count. All Python tests used the required isolated-file `scripts/run_tests.sh` runner. Generated TS/OpenRPC freshness, shared and TUI typechecks, Ruff on 105 changed Python files plus the final binding regression, and diff checks passed. Desktop typecheck initially exhausted Node's default 2GB heap; a bounded 4GB retry is recorded separately below. The previous full campaign and host-denied browser/socket cases are unchanged; no full-suite or live qualification is claimed here. Dots will pin the actual published producer commit and repeat the Node-to-Python consumer lifecycle before BE03 acceptance. No deployment, real provider activation, Slack send, live computer action, LAYA activation or credential change occurred.

Desktop consumer verification: the complete desktop typecheck passed with NODE_OPTIONS=--max-old-space-size=4096; no code or TypeScript configuration was changed to obtain that result.

## Dots BE06 prerequisite repair — Exact JSON workflow review

The actual Dots-to-Python workflow consumer exposed a false positive in exact approval detail: the required benign JSON run manifest is transported as base64 beginning with `eyJ`, which the generic JWT redactor treated as credential material. Changed only the review scan input after existing wrapper, digest, MIME and size validation: scan exact decoded UTF-8 content plus all action/content metadata and bound revisions, excluding the transport data field. Immutable stored review bytes, review digests, approval bindings and the global redactor are unchanged. Sensitive content is still withheld rather than presented as a redacted exact review.

Baseline red evidence reproduces the benign JSON failure while three credential-bearing cases remain withheld. Final canonical isolated-file qualification passed 24 tests across the approval-detail and workflow RPC suites, with no failures, skips or retries. The real producer workflow path evaluates and promotes a version, prepares varied runs, verifies every exact output review including the manifest, publishes immutable artifacts and reads canonical history. Added checks preserve exact base64/UTF-8/content/review digests, refuse synthetic JWT/API credentials in both action and decoded content, reject malformed wrappers and digest/MIME/payload mismatches, and keep digest-consistent invalid UTF-8 opaque. Ruff, compileall and diff checks passed. Exact source/log hashes and boundaries are in `docs/build/dots-be06-review-validation.json`.

This isolated repair starts from `9c39b3cbc7d23c65782e0f73f8c8102d07955e2e`; it contains no BE07 scheduler changes or generated contract delta. Parent-owned producer publication and Dots re-pin/Node-to-Python consumer qualification remain separate. No full-suite claim, external publication, dependency installation, deployment, real credentials or live provider use occurred.

## Dots BE07 prerequisite — Observed stdio scheduler lifecycle

Prepared the scheduler prerequisite on published BE06 commit `3453afefce2b21947390fac0e03d9eaa67f326e9` (tree `ed2801696dd28e144e8325073ffeb81f764e5ca3`), preserving the exact BE06 repair and this existing journal. Original local scheduler commit `1a95dab05aea438fab66e298c4d086d9665372ba`, including its original qualification journal, is retained on local branch `dots-be07-producer-original`. The nine implementation, contract, documentation and test files are byte-identical to that reviewed local unit; its journal is consolidated here under the repository's lowercase filename.

Stdio scheduling remains disabled without explicit boolean `cron.stdio_scheduler.enabled: true` in trusted profile configuration. The existing admission maintenance handle drives the ordinary cron ticker, with no additional timer or competing Dots execution loop. The profile `.tick.lock`, retirement/ESTOP gates, atomic occurrence/command admission, queue launch leases and turn leases remain authoritative. Another live gateway owner causes stand-down. The existing admission lock excludes shutdown through the clock-to-queue handoff; shutdown then cancels accepted queue work and fences future ticks.

The owned, profile-scoped `runtime.schedule.scheduler.status` read requires fresh observed maintenance and successful actual locked-tick evidence before reporting recurring readiness. Configuration or a callback handle alone is insufficient. Status reads do not dispatch, and do not disclose paths, PIDs, credentials or raw exception messages. Lifecycle coverage includes absent/non-boolean opt-in, non-stdio exclusion, two real recurring admissions with exact budget debit and no duplicate instant, lock contention, other-owner stand-down, read-only and stale status, safe error projection, profile A→B→A isolation, and a concurrent shutdown handoff. The separate execution test covers queue pump/AIAgent/result-outbox behavior with a synthetic provider.

Supported-runtime gate on Python **3.14.7**: canonical `scripts/run_tests.sh` with `HERMES_PYTHON=/tmp/dots-be12-py314-prep/test-venv/bin/python`, `HERMES_TEST_FILE_RETRIES=0`, and two workers passed **14 tests across three files, zero failures, skips or retries**: `tests/tui_gateway/test_stdio_schedule_lifecycle.py` (11), `tests/tui_gateway/test_command_schedule_execution.py` (1), and `tests/tui_gateway/contracts/test_generated.py` (2). Direct contract generation check under the same Python passed; focused Ruff and staged diff checks passed. No broad suite was repeated. Earlier Python 3.12 qualification remains historical evidence on the preserved local branch: 142 focused tests and 31 separate provider regressions, plus a base-maintenance negative lifecycle probe that failed as expected and passed after restoring the fix. Those runs are not added to this current 14-test gate.

Generated TypeScript SHA-256 remains `19fdf11f4aa587d7bcdd58927c0cdbbdec463cfdeaa90003e0ac6284266539d2`; OpenRPC SHA-256 remains `fa7b08c3edd56190daadeef533928685113fd70a2ae8959b2fb7b61aee5051b2`. The exact new method is `runtime.schedule.scheduler.status`. Both generated artifacts match regeneration on the current BE06 base.

Qualification is limited to this focused producer prerequisite. Full Dots HTTP → stdio subprocess integration, frontend recurrence/history parity, live providers, external delivery and deployed operation remain separately qualified work. Enabling this option would also tick pre-existing Ryoko cron jobs, requiring operator review; migrated legacy Dots admission must remain fenced. No real schedule/configuration, credential, dependency, provider or deployment activation occurred. This commit is local only; producer publication and consumer re-pinning remain with the integration owner.

## 2026-10-05 LAYA integration planning

**Status:** planning complete; L00–L10 implementation and qualification remain pending. Reviewed current `main` at `a45b0d9b3202691da805b954336135d7ba7378f7` and the supplied, unverified LAYA systemone API description.

**Delivered:** root [LAYA_Integration_BuildPlan.md](LAYA_Integration_BuildPlan.md), with links from the backend roadmap and existing LAYA policy/node documentation. The plan covers DP16-first context and catalog assembly, exact prompt/API examples, closed-response adaptation, native staged batching and same-family multi-tool selection, secure cross-machine transport, private-data authorization, budget/receipt bounds, real owner/cache integration, existing-hardware evaluation, measurable promotion gates, and rollback. It separates existing code, proposed work, and user-owned tunnel/auth prerequisites; Observatory remains telemetry-only.

**Verification:** source review and independent technical cross-check; documentation structure/local-link checks, both synthetic JSON examples parsed, phase coverage and documentation-only diff/whitespace checks. No application tests, CI, model calls, credentials, network setup, training, private-data export, deployment, or activation. Actual endpoint/auth details, loaded-model evidence, privacy qualification, latency/quality measurements, and rollout approval remain pending. No secret source file or credential value is included.

**Publication:** direct-main documentation checkpoint; remote SHA and exact changed files are verified after publication and reported in chat. This entry is planning evidence only, not implementation or runtime readiness.

## 2026-10-05 LAYA connection-details planning update

**Status:** planning-only correction on base [040dfe75904877be1cfe6a1df3367164cca6598d](https://github.com/TianJieHeng/ryoko-agent/commit/040dfe75904877be1cfe6a1df3367164cca6598d). L00–L10 implementation and qualification are not completed by this entry.

**Updated:** [LAYA_Integration_BuildPlan.md](LAYA_Integration_BuildPlan.md) and its backend roadmap summary now record the corrected operator-supplied inference route `POST https://laya.ryoko.okinawa/v1/systemone`, JSON with a Bearer `LAYA_API_KEY` secret reference, and unauthenticated `GET https://laya.ryoko.okinawa/health`. The supplied runtime is `i1` ONNX fp16. Only inference and health are reported exposed; dashboard/activity are reported unexposed, and Observatory remains telemetry-only.

**Evidence level:** the operator note reports authenticated HTTP 200 with a sample `memory_search` result at confidence `0.9452`, and missing-key HTTP 401 on 5 October 2026. These outcomes were not independently reproduced in this change and are not a full test suite, calibration, or release evidence. Existing thresholds remain unchanged. The tunnel reportedly starts at boot while the model starts at desktop login; restart without login and subsequent recovery remain explicit qualification cases.

**Open prerequisites:** approved secure credential provisioning, real-host endpoint/auth/route acceptance, service and loaded-artifact identity, network/intermediary and local logging/privacy qualification, end-to-end performance/quality evidence, and exact-scope rollout approval. No credential value or source secret file is included.

**Verification scope:** documentation-only content, whitespace/diff, unchanged synthetic JSON examples, and link checks. No code, configuration, environment, workflow, or runtime changes; no model/server calls, application tests, CI, paid compute, deployment, or activation. The publication receipt verifies the exact remote files and commit; this journal entry does not claim live readiness.

## 2026-10-05 L00 — contract and baseline frozen

**Status:** complete for offline contract freeze; production remains off. Base `37d78be063d7876c92c54bc4a4b91ec8ace7fdd6` preserves the corrected endpoint documentation. User authorized phase-by-phase implementation; separate credential/private-data/rollout gates remain unchanged.

**Delivered:** ADR 013 in `docs/build/laya-integration-contract.md`, versioned `laya-integration-readiness.json`, linked architecture/node documentation, and updated plan implementation status. DP16 v2 renderer/catalog/body/stage/receipt bounds are explicit; existing v1/LAN behavior stays compatible. Configured release hashes and measured loaded-artifact evidence are distinct. Unknown deployment semantics and Jetson boot-without-login availability have named owners.

**Checks:** baseline diff is documentation-only since reviewed `a45b0d9`; readiness JSON parsing, source-fingerprint verification, internal links, changed-document secret-pattern review, `git diff --check`, and no-active-workflow checks passed. No live endpoint request. Existing pinned Python 3.14.7 restored locally without dependency mutation; canonical smoke `HERMES_PYTHON=<existing-test-interpreter> scripts/run_tests.sh tests/test_hermes_yaml.py` passed 9 tests (runtime restoration evidence only). Available local Linux resources inventoried; no CI or paid compute.

**Open gates:** operator-controlled secure credential entry; real Ryoko/Jetson endpoint and loaded-artifact evidence; privacy/retention/custody and exact-category authorization; full-plan performance and independent quality/net-benefit evaluation; exact-release rollout approval. None blocks the independent synthetic implementation phases. Rollback remains off/no-traffic; no runtime config changed. Next: L01 authorized description-rich catalog. The containing Git commit identifies this checkpoint and is verified remotely after push.

## 2026-10-05 L01 — authorized description-rich catalog

**Status:** complete; base `53b6fa723fb5fc662e3795476a898c66b601ad2c`. Added `planner_catalog.py` and extended `LiveCatalog`/`live_catalog` without changing v1 planning or bundle installation. Catalog v2 includes bounded descriptions/input-purpose hints, explicit family membership, trusted source/schema hashes, scope/policy/tool-view binding, and exact one-to-one aliases for IDs outside the wire grammar. Original full description/schema changes invalidate the hash even when redaction/truncation yields the same visible text. Unknown dynamic families use a described conservative session family; schema claims cannot self-certify read-only effects.

**Authority:** current live identity and discoverable session view filter membership before descriptive metadata is assembled; final live-policy check fences the completed snapshot. No availability probes, handlers, memory calls, private export or new grants. Current snapshot, dispatch, recovery and eventual installation still check authority independently.

**Focused validation:** `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 tests/agent/test_decision_planner_catalog.py tests/agent/test_decision_tool_planner.py tests/agent/test_decision_planner_runtime.py` passed **27 tests in 3 files**, zero failures/skips/retries. Final catalog rerun including the additional explicit-session fallback case passed **16/16**. Coverage includes real profiles and primary/specialist A→B→A isolation, denied personal-MCP metadata, live revocation/discoverability, schema/description/source/policy drift, malicious controls/credential-bearing URLs, bounds, exact aliases/collisions and existing real observer/cache behavior. `git diff --check` passed. Synthetic local evidence only; no CI or live service call.

**Limitations/next:** classification quality and real catalog-size feasibility remain qualification work. Incumbent behavior is preserved on catalog failure; production remains off. Next: L02 bounded context and deterministic renderer. Journal and remote main checkpoint are published together; containing commit identifies the reviewed changes.

## 2026-10-05 L02 — bounded context and deterministic prompts

**Status:** complete; base `255a3ca5a4cca26c4d6a2e10a65ce29e09c26044`. Added pure `planner_context.py`, versioned `laya_prompts.py` and their focused tests. The builder accepts only explicitly selected owner facts: current request/goal/constraints, at most four relevant visible messages, two bounded tool status summaries, sourced references/corrections and attachment metadata. It never reads memory, credentials, files, attachments, system prompts, reasoning or a network. Redaction preserves the original data classification. Unknown antecedents, absent required attachment contents, useful-data overflow and unsupported inputs cause explicit incumbent fallback.

**Protocol:** fixed `dp16-systemone-v1` instructions, closed need/effort/family/inclusion/verification criteria, deterministic alias bindings and prior-stage facts; untrusted catalog/context stays outside instructions. The full canonical state shares a strict 12 KiB byte bound with catalog data. Exact 12,288-byte fixture accepted and one-byte overflow rejected; no silent clipping of identifiers or candidate membership. Context fallback remains bound even if a caller copies the JSON values.

**Focused validation:** `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 tests/agent/test_decision_planner_context.py tests/agent/test_laya_prompts.py` passed **62 tests**, zero failures/skips/retries. Coverage includes follow-up ambiguity and evidence, destination corrections, multilingual/UTF-8 and JSON escaping, tool failures, injected instructions, image/PDF/inline-data handling, secret URLs and control-split synthetic tokens, deterministic rendering, alias/scope/membership binding and input immutability. `git diff --check` passed.

**Limitations/next:** lexical reference detection is deliberately conservative; no retrieval/resolution-model accuracy claim. Redaction is not private-data consent. No live requests, CI, paid compute or production activation. Next: L03 strict systemone codec and explicit v2 contracts; containing commit identifies this phase and remote tree verification follows publication.

## 2026-10-05 L03 — strict systemone codec and explicit v2 contracts

**Status:** complete offline; base `93ae528257470b60af31e262d76c50ad1e30feb7`. Added transport/credential-independent `laya_codec.py` and sanitized synthetic API fixtures. `contract_for("DP16", 2)` and `build_state(..., contract_version=2)` are explicit; default registry/v1 receipts and existing LAN contracts retain their identities. Each physical batch retains unique nonce-bearing wire IDs mapped to immutable typed requests whose input digest binds that question's exact family/tool/shortlist. Shared state is serialized once.

**Validation rules:** exact choice envelope/question/type/option sets, duplicate/deep/oversized JSON rejection, finite non-boolean probabilities, sum tolerance 1e-6, argmax/confidence consistency, ties abstaining, strict aliases/scope/stage/prior/menu binding, mixed/stale batches rejected, local result envelopes validated against expected bundle. Usage is one validated batch record, never multiplied per answer. Private encoding is independently denied. Model aliases/configured hashes remain expectations, not loaded-weight attestation. Malformed numeric/Unicode inputs raise fixed safe errors.

**Focused compatibility command:** `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> scripts/run_tests.sh tests/agent/test_laya_codec.py tests/agent/test_decision_client.py tests/agent/test_decision_transport.py tests/agent/test_decision_point_policies.py tests/agent/test_decision_calibration.py tests/agent/test_laya_prompts.py tests/agent/test_decision_planner_context.py tests/agent/test_decision_planner_catalog.py` passed **277 tests in 8 files**, zero failures; codec-specific coverage is 68 tests. Fixture JSON parsing and `git diff --check` passed. No CI or live requests.

**Measured fixture limit:** the fixed question instructions and JSON escaping can exhaust the 32 KiB encoded body before the nominal 32-candidate count. A 22-tool synthetic batch correctly rejects instead of silently dropping candidates, truncating instructions or widening limits. Actual deployment numeric/envelope behavior, artifact identity and latency are unmeasured. Next: L04 bounded native staged planner, shared destination admission and complete receipt projection. The containing phase commit and remote tree are verified after publication.

## 2026-10-05 L04 — native stages, multi-tool shortlist and bounded accounting

**Status:** complete offline; base `b0239c4f661b321d629d4bf2be808f406863fa18`. Native DP16 v2 uses one HTTP-equivalent call per independent stage, includes multiple tools in one family, verifies every selected tool against the complete shortlist, and preserves v1. Whole-plan fallback applies to uncertain/contradictory responses, missing bridges, stale scope/catalog, caps, byte limits, expired deadlines and incomplete receipts. Stage 1 now exports only versioned family summaries with authoritative member counts/digests and the full catalog hash; native admission requires exact need/effort/all-family coverage. Stage 2/3 export only selected-family descriptors. Low-level explicit codec diagnostics retain their strict earlier shape.

**Runtime bounds:** one process-wide in-flight slot and shared circuit per physical destination survives new clients, profiles, config and credential rotation. Caller timeout never starts a replacement inference; unknown remote completion remains quarantined for the process, with no config/model reset flag. Independent bounded receipt workers persist each batch within the original shared deadline and stop subsequent writes when abandoned. Release-before-completion ordering prevents dependent-stage capacity races. Metrics count usage once per batch; the plan carries every persisted receipt (v2 maximum 62), and generated projections preserve strict historical v1 bounds.

**Budget/fence:** the real observer now reserves/settles once around the complete planner operation, including catalog/context work, every stage and receipts. Uncertain completion retains an unknown durable reservation instead of pretending remote work ended. Final plan publication rechecks live run/identity under the control lock. No main-provider attempt debit or retry-controller change.

**Focused checks:** seven-file native/legacy command (`test_decision_planner_v2.py`, `test_laya_batching.py`, `test_laya_stage1_projection.py`, `test_laya_codec.py`, `test_laya_prompts.py`, `test_decision_client.py`, `test_decision_tool_planner.py`, all under `tests/agent/`) passed **173 tests**. Additional canonical budget-ledger tests passed **2/2**, plan projection **2/2**, generated contract check **2/2**. Commands use `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> scripts/run_tests.sh`; focused parent runs use `-j 2` and `HERMES_TEST_FILE_RETRIES=0`. Generated TypeScript/OpenRPC were regenerated with `scripts/gen_gateway_contracts.py`. Initial budget-test collection found an incorrect local test import; corrected to the repository's existing test package, then rerun passed.

**Behavioral evidence:** 102-tool catalog with two families routes using summary-only first-stage data and two selected tools; no unnecessary individual-tool descriptions in that first body. The 12-selected-tool boundary passes with 29 complete receipts; 62-receipt replay is independently tested. Byte caps can still reject a request below nominal count caps, intentionally. No real-service performance/quality claim, CI, paid compute or credential use. Detached committed-tree validation passed **179 tests across those ten files**, zero failures/skips/retries, using `-j 2` with retries disabled; only this journal result was added afterward. Next: L05 explicit secure transport/configuration.

## 2026-10-05 L05 — exact secure destination and private-data gates

**Status:** offline implementation complete; real-host acceptance remains blocked. Base `724cdcc88bb6d8061e817e350d684b3be548d3fe`. Added `LayaDestinationManifest`/`LayaHttpsTransport`, explicit schema-2 profile selection, exact `decision_inference` recipient purpose, and the systemone operator runbook. Existing pinned LAN transport is unchanged. Configuration remains off unless explicitly selected and rejects enforcement, private classes, inline keys, arbitrary URLs and test injection.

**Security:** exact hostname/path/method, normal hostname-verified TLS, no proxies/redirects/retries, one validated public DNS sockaddr with connected-peer check, absolute resolver/TLS/header/body deadline, capped bodies, safe errors, uncertain-completion quarantine, and repeated current owner/run/recipient/secret-reference checks before bytes. The existing profile secret mechanism resolves only `LAYA_API_KEY`; no ambient legacy-secret fallback is admitted. Client, codec and transport each keep private data blocked. Python-only local TLS fixtures accept only synthetic packets/fake keys and are excluded from config. Health requires its own exact grant and proves liveness only.

**Focused exact-isolation validation:** applied only L05 files atop the L04 detached candidate and ran `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 tests/agent/test_laya_transport.py tests/agent/test_laya_destination_authorization.py tests/agent/test_laya_configuration.py tests/tools/test_egress_policy.py tests/agent/test_decision_transport.py`: **113 tests passed in 5 files**, zero failures/skips/retries. Covers actual local TLS, wrong host/CA, private/DNS/peer rejection, redirects and 401/403/413/429/500, slow/incomplete/oversized replies, revocation during DNS/TLS, zero-body private refusal, profile A→B→A isolation, safe config and legacy LAN compatibility. `git diff --check` and local doc links passed.

**Open gates:** no real key was read, installed or transmitted, and no live LAYA request was performed. Secure operator-controlled provisioning, actual service identity/API/route acceptance, origin/intermediary retention/privacy, hardware/quality evidence and rollout approval remain open. No CI, server/tunnel changes or paid compute. Next: L06 production observer/context/receipt integration. Publication uses bounded per-artifact tree staging after the earlier aggregate upload stall; no separate remote blob creation or CI is needed.

## 2026-10-05 L06 — real front-door observer and operational receipts

**Status:** complete for synthetic owner-path validation; base `8c5962c769803db1801e3a3c8c9a400c0a406b02`. The production front-door seam now assembles explicitly owned context, builds the authoritative catalog, runs the native codec/transport/planner and records one bound result. Run/turn/generation/session/scope/request bindings prevent prior-turn or duplicate reuse. The late hook reuses an already prepared exact result; provider retries/tool rounds never classify again. A concrete earlier preparation seam is available to the cache owner in L08, without mutating late provider kwargs.

**Context/authority:** visible selected messages and bounded status-only tool summaries, active owner goal and metadata-only attachments; no raw tool payloads, system/reasoning fields, memory lookup or attachment fetch. Normal input remains private. Explicit fixture-origin classification is test-only. Duplicate authorized schemas are rejected instead of silently deduplicated. Source/status normalization and multipart overflow are closed. Receipt/policy writes and final plan application recheck current run under its control lock. The complete operation uses one existing budget reservation; uncertain remote settlement stays unknown.

**Observability:** generated plan projections include renderer/context digests, redaction/omission counts, classification-admission status, closed fallback, complete receipt IDs and once-per-batch timing/usage metrics. They never include raw state or authentication headers. Actual installed-prefix recovery metadata is prepared for L08 and remains distinct from shadow-only omissions.

**Exact phase validation:** staged only L06 files atop the L05 detached candidate, regenerated contracts there, then ran `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 tests/agent/test_laya_observer_integration.py tests/agent/test_decision_planner_runtime.py tests/agent/test_decision_runtime.py tests/agent/test_laya_budget_settlement.py tests/agent/test_laya_plan_projection.py tests/tui_gateway/contracts/test_generated.py`: **22 tests passed in 6 files**, zero failures/skips/retries. The assembled working-tree owner/runtime combination also passed **58 tests**. Initial TLS fixture setup wrote a fake secret after agent construction, correctly tripping the existing immutable memory/credential-scope guard; moved fixture secret creation before construction and reran cleanly. No guard was weakened.

**Actual path evidence:** real AIAgent admission, existing owner/secret/destination checks, locally generated hostname-verified TLS, all three native batches, real fenced journal/replay and provider requests. Complete mocked-provider request bodies equal the off baseline in shadow. Disabled/private/missing-bridge paths transmit zero bytes; late canceled results never publish a plan; storage failure stops dependent stages; retry/duplicate hooks preserve one observation; A→B→A profile scopes and fake keys stay isolated. `git diff --check` passed. No live endpoint, credential entry, CI, paid compute or production activation. Next: L07 qualification tooling and explicit actual-host blockers; independent L08 owner work can proceed while those gates remain open.

## 2026-10-05 L07 — offline inspector delivered; live qualification blocked

**Status:** blocked for actual runtime/checkpoint qualification; offline tooling complete. Base `18aa40685b7a2129f939ea5e338163293eb809af`. Added closed redacted-evidence inspector `evals/decisions/qualify_laya.py`, synthetic input/report fixtures, and `docs/build/laya-runtime-qualification.md`. This is not a fabricated passing qualification or a new training task. It preserves all release gates and reports actual-host/auth/artifact/provenance/privacy/performance/independent-outcome/rollout evidence as missing.

**Measurements:** one unauthenticated normal-TLS GET to the supplied public `/health` from the assistant cloud at 2026-10-05T10:33:25.907Z returned HTTP 403, application/json, 708 bytes. The response origin (service versus intermediary) and health are unverified; no raw body was published. An earlier web reader could not access this URL. No authenticated inference, real key entry/transmission, other route probe or actual Ryoko/Jetson-host qualification occurred. The prior operator-reported 200/401 remains separately labeled.

**Inspector:** exact units/accounting, one classifier charge per batch, full network/queue/context/stage/receipt latency, task output/schema/bridge/recovery/cache accounting, unknown completion/usage preserved, and actual reduced-bundle misses separated from shadow omissions. Independent-episode statistical bounds, explicit assumptions and every failed/missing gate are inspectable; saved assertions cannot authenticate source evidence. Review corrected fixed-bin ECE to a justified one-sided McDiarmid bound; conservative count-ratio recall/recovery intervals can still be inconclusive under the sample cap even with perfect observations, and are documented as an evidence limitation instead of weakening gates.

**Validation:** `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 tests/agent/test_laya_qualification.py tests/agent/test_decision_planner_evaluation.py` passed **42 tests in 2 files**, zero failures/skips/retries; the same L07-only snapshot was checked atop the published L06 tree. CLI `python -m evals.decisions.qualify_laya evals/decisions/laya_qualification_fixtures.json --readiness docs/build/laya-integration-readiness.json --output docs/build/laya-synthetic-qualification.json` produced 35 blockers and all production/L07/L09/L10 qualification/authorization flags false. JSON/doc-link/whitespace checks passed.

**Required next inputs:** secure operator-controlled key provisioning and actual host access; measured loaded-artifact/route/hardware evidence; private destination/retention/custody qualification and consent; independently labeled frozen holdouts and paired outcomes; exact rollout approval. No CI, paid compute, server/tunnel changes, private export, new model download or training. Independent L08 cache-owner implementation proceeds behind these gates; L09 real-candidate qualification and L10 rollout cannot be marked complete.

## 2026-10-05 L08 — qualified cache owner and bounded durable publication

**Status:** implementation complete under synthetic qualified-owner tests; actual production qualification remains blocked. Base `159477f26f355640be5845161340ed0f0d03be1c`. Added the real earlier first-schema-freeze and committed-compression owner, exact durable request-schema pins, explicit v2 release/renderer/catalog/evaluation bindings, and closed bundle projections. Canonical discovery remains complete. Installation independently validates same-run/turn/generation/scope/catalog/release and every persisted receipt through the existing policy book. Config still cannot enable enforcement; synthetic trusted-host capabilities do not authorize a deployed release. See `docs/build/laya-bundle-owner.md`.

**Cache and recovery:** same-context follow-ups, retries and fresh-agent resume retain exact schema bytes; failed compaction never opens a boundary. Successful compression requires its durable projection witness. Restored prefix uncertainty must recover the exact pin or defer dispatch; a warm valid pin survives read failures. Bridge discovery/recovery stays authorized independently, real reduced-prefix misses are distinct from shadow omissions, and rollback preserves the live prefix until a real boundary. Native/detached or legacy-rotation paths without the required witness remain non-enforcing. Default-off multipart requests are not subjected to planner-only digest limits.

**Review fixes:** optional receipt, policy and plan storage no longer holds the lifecycle control lock; bounded worker admission fences transactions before/after insertion. Cancellation and finalization can progress while a pretransaction write is stalled. Precommit abandonment rolls back; post-final-guard unknown commit or real storage uncertainty blocks dispatch. A genuine SQLite/filesystem transaction stall can still obstruct the shared store; no stronger storage guarantee is claimed. Capacity remains occupied until worker exit. Batch uncertainty is captured per submission before next admission, preventing unrelated destination quarantine from falsely retaining another operation's budget reservation. Independent review closed all four scoped findings after deterministic regressions; the old lock defect was reproduced red before verifying the fix.

**Local checks:** final expanded run passed **113 tests in 8 files**, zero failures: `test_laya_journal_deadlines`, `test_decision_runtime`, `test_decision_planner_runtime`, `test_decision_client`, `test_laya_batching`, `test_laya_observer_integration`, `test_laya_budget_settlement`, `test_laya_bundle_owner` (all under `tests/agent/`, `.py`). After final abandonment exception cleanup, journal/batching/client revalidation passed **41/41**. Core runtime-store/command compatibility passed **30/30**. Commands: `HERMES_PYTHON=<existing-Python-3.14.7-test-interpreter> scripts/run_tests.sh -j 2 --file-retries 0 <listed files>`. The owner file includes **53** actual owner/recovery tests. Behavioral compatibility fixtures use a one-second explicit test timeout; production's 150 ms default is unchanged and independently exercised by complete-operation stalled-policy tests. Per-submission uncertainty regression passed within a **15-test** batching/budget run. No retries, CI, live inference or paid compute.

**Static evidence and next:** shared TypeScript typecheck, configured Ruff checks and `git diff --check` passed. Generated-contract and release-policy checks passed **50/50** after refresh; they will also be included in the combined L09 campaign at the verified L08 candidate SHA. No actual model, privacy, latency, net-benefit or release approval gate was weakened. Next: bounded assembled local campaign and honest L09 qualification/ L10 rollout handoff; both actual live gates remain blocked.
