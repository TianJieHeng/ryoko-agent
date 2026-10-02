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
