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
