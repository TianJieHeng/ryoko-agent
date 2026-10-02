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
