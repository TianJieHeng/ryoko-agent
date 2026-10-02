# Ryoko Frontend Build Plan

This plan chronicles the user-facing behavior of the upgraded runtime through the existing CLI, TUI and control surfaces, then the separate OpenDots handoff. It is a product/control plan rather than a new dashboard design. Use it with [BackEnd_BuildPlan.md](BackEnd_BuildPlan.md) and the shared [buildjournal.md](buildjournal.md).

**Status:** implementation plan only. This documentation change implements no application behavior, deploys no service and does not claim tests or benchmarks passed.

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

## Current frontend and control surface review

| Existing surface | Verified seam | What the plan changes |
|---|---|---|
| Classic CLI | [hermes_cli/commands.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/hermes_cli/commands.py) | Use central CommandDef registry and topical mixins; register proposed controls once |
| Ink TUI | [ui-tui/src/app/createGatewayEventHandler.ts](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/ui-tui/src/app/createGatewayEventHandler.ts) | Extend existing event/state rendering and preserve foreground/focus |
| Shared RPC schema | [tui_gateway/contracts/registry.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tui_gateway/contracts/registry.py) | Add Pydantic contracts and regenerate consumers; no handwritten divergent protocol |
| Shared clients | [apps/shared/src/json-rpc-gateway.ts](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/apps/shared/src/json-rpc-gateway.ts) | Preserve reconnect generation and request handling while adding durable semantic cursors |
| Project RPC | [tui_gateway/methods_projects.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tui_gateway/methods_projects.py) | Extend existing per-profile multi-folder project controls |
| Goal/loop/heartbeat controls | [tui_gateway/methods_session_control.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tui_gateway/methods_session_control.py) | Project the existing shared manager then add mission semantics |
| Pending approval/clarify requests | [tui_gateway/server_requests.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tui_gateway/server_requests.py) | Preserve open-request replay/cancellation and add exact-action binding |
| Dashboard chat | [web/AGENTS.md](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/web/AGENTS.md) | Keep PTY-backed real Ink chat; no duplicate React composer/transcript |
| Desktop | [apps/desktop/AGENTS.md](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/apps/desktop/AGENTS.md) | Existing Electron renderer and shared backend; compatibility/minimal controls, no redesign |
| Existing desktop project/artifact/capability/status components | [apps/desktop/src/AGENTS.md](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/apps/desktop/src/AGENTS.md) | Reuse established ownership and store patterns |
| Existing replay tests | [tests/tui_gateway/test_tui_gateway_event_replay.py](https://github.com/TianJieHeng/ryoko-agent/blob/b78931e3b0959c42dca7400c78a4dffd1bb48575/tests/tui_gateway/test_tui_gateway_event_replay.py) | Preserve current bounded-ring behavior while testing new restart semantics |

The fork already has web, TUI and desktop interfaces. “Frontend” here does not mean inventing a dashboard because the old source plan predates those surfaces. Proposed cards and views can first be concise CLI/TUI summaries and commands. Existing shared consumers receive compatible state and necessary controls. The full OpenDots workspace/channel experience belongs in `ryoko-dots`, following FE14.

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

## Frontend phase index

| Phase | Work package |
|---|---|
| [FE00](#fe00) | Inventory existing surfaces and agree shared contracts |
| [FE01](#fe01) | Show identity configuration and usable connections |
| [FE02](#fe02) | Support projects capture resumption and scoped memory |
| [FE03](#fe03) | Deliver complete files precise revisions templates and branches |
| [FE04](#fe04) | Make missions steerable and completion truthful |
| [FE05](#fe05) | Expose connected research living briefs and decisions |
| [FE06](#fe06) | Make workflows monitors and conditions understandable |
| [FE07](#fe07) | Package data teaching tutoring and creative production |
| [FE08](#fe08) | Turn communications into reviewed follow-through |
| [FE09](#fe09) | Show coding browser and device work with real outcomes |
| [FE10](#fe10) | Expose specialists teams voice screen and handoff |
| [FE11](#fe11) | Provide useful status setup repair and opportunity controls |
| [FE12](#fe12) | Explain and control LAYA decisions without exposing authority |
| [FE13](#fe13) | Validate complete user journeys and release behavior |
| [FE14](#fe14) | Hand the stable runtime contract to the separate Dots fork |

## Frontend implementation phases

<a id="fe00"></a>

### FE00 Inventory existing surfaces and agree shared contracts

**Status:** planned. **Objective and value:** Establish what FrontEnd means in this fork: current CLI/TUI/control interfaces, necessary compatibility for existing desktop/web clients, and a later separate OpenDots consumer.

**Prerequisites:** BE00/BE01; contract work pairs with BE02 before broad UI changes.

**Files and ownership:** Existing: cli.py, hermes_cli/commands.py, hermes_cli/cli_commands_mixin.py, tui_gateway/contracts/, apps/shared/src/gateway-contract.generated.ts, apps/shared/src/json-rpc-gateway.ts, ui-tui/src/app/, web/src/pages/ChatPage.tsx, apps/desktop/src/app/. Proposed: small typed view models beside owning features, not a new UI framework. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Trace project selection, prompt submission, session resume, server requests, control actions, artifacts and connection setup across CLI, Ink TUI, messaging and existing shared clients. Record supported, missing and partially implemented behavior at the pinned SHA.
2. Adopt the backend object vocabulary: project, mission, artifact, knowledge reference, workflow, monitor, commitment, specialist and connection. Record each authority and fields needed by users; do not create an independent frontend task or memory database.
3. Extend the existing generated Python/Pydantic contracts and shared TypeScript client. Runtime commands carry stable operation identity/revision and responses carry committed status. Frontend-supplied labels never confer scope.
4. Preserve the dashboard’s real PTY/Ink chat rather than recreating transcript/composer in React. Existing React inspectors may project structured status. Desktop is a separate renderer on the same backend; maintain contract compatibility without a redesign.
5. Define transport state separately from work state: offline/reconnecting does not mean cancelled, and an ended stream does not mean completed. Capability negotiation must show unsupported features and version mismatch clearly.
6. Use the existing command registry/table-driven dispatch and feature-local nanostores. New CLI commands below are proposed interfaces to register centrally, not claims that they already exist.

**Interface and data contract:** Consume RuntimeCapabilities, IdentityBinding, RuntimeCommand/CommandReceipt, RuntimeEventEnvelope and MissionSnapshot from BE02. Rendering models contain only authorized projections; no credentials, opaque model state or private traces.

**Failure and security boundary:** No second loop, scheduler, permission engine or memory authority in a UI. Unknown capabilities disable the action with a useful explanation. Stale responses cannot overwrite newer selection.

**Focused checks:** Focused: existing gateway generation tests; one protocol fixture for each new object; replay/snapshot race and version error; same command through CLI and TUI; web PTY regression.

**Acceptance and exit:** One agreed producer/consumer schema and surface inventory exists. Every proposed control has an authoritative backend operation and an honest unsupported/degraded state.

**Integration and rollback:** Additive compatible fields first; retain supported old client behavior behind negotiated capability. Revert a view without changing runtime state or replaying commands.

**Chronicle and Git checkpoint:** append a `FE00` entry to root [buildjournal.md](buildjournal.md); branch `build/fe00-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe01"></a>

### FE01 Show identity configuration and usable connections

**Status:** planned. **Objective and value:** Make F34 and per-agent ownership understandable before users act through new capabilities.

**Prerequisites:** BE01/BE04/BE05 plus FE00. Minimum setup ships in Slice A; broader setup remains FE11.

**Files and ownership:** Existing: hermes_cli/config.py, hermes_cli/tools_config.py, hermes_cli/mcp_config.py, tui_gateway/methods_config.py, tui_gateway/methods_connectors.py, ui-tui/src/app/setupHandoff.ts, apps/desktop/src/app/capabilities/. Proposed: redacted agent/effective-policy inspect rendering. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Present current agent, project, execution location and effective model/backend only when relevant to the task. Distinguish profile, stable specialist, ephemeral child and remote executor; no persona/name shortcut to identity.
2. Offer a read-only effective-config explanation with source of overrides, scope, budgets, approval mode and memory backend. Hide secret values. Explain next-session/restart/cache-aware changes rather than silently applying them to a running conversation.
3. Connection setup tests the actual requested read/tool path, including WebSocket/auth or service health as appropriate; a stored token alone is not a connected capability. Show operation scope, missing capability and smallest repair step.
4. Ryoko memory settings show the personal external harness binding and acknowledged capabilities. A specialist sees its isolated built-in memory and granted project artifacts; it cannot select the primary harness from a dropdown.
5. Connection/revocation state updates all stale tool views. Scope changes are submitted to backend policy; the frontend never manufactures or stores broad credentials in ordinary component state.
6. Keep setup recoverable after cancel, timeout, failed probe or navigation. Return to the user’s current work without replacing it with an onboarding flow.

**Interface and data contract:** ConnectionView {identity, granted_operations, status, last_verified_at, failure_class, repair_action}; AgentMemoryView {agent_id, backend_kind, scope, supported_operations, health}; config inspection is a redacted projection.

**Failure and security boundary:** No leaked credentials, false connected badge, unauthorized permission escalation, silent backend migration or treating a remembered preference as a grant.

**Focused checks:** Focused: failed actual capability probe, revoked access, stale async probe after agent switch, cancel/reopen setup, Ryoko versus specialist settings, copy/rename does not copy privileges.

**Acceptance and exit:** P01/P02 setup prerequisites are discoverable and honest; an unavailable operation explains exactly what blocks the task.

**Integration and rollback:** Revert UI changes independently; retain server grants and pending setup receipts. Never imply disconnect by merely hiding a tool.

**Chronicle and Git checkpoint:** append a `FE01` entry to root [buildjournal.md](buildjournal.md); branch `build/fe01-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe02"></a>

### FE02 Support projects capture resumption and scoped memory

**Status:** planned. **Objective and value:** Deliver F01/F03/F04/F05/F06 using current controls and explicit scope.

**Prerequisites:** BE07/BE08 and FE01; capture can arrive after the first project/artifact slice.

**Files and ownership:** Existing: hermes_cli/projects_db.py, tui_gateway/methods_projects.py, tui_gateway/methods_session.py, ui-tui/src/app/sessionResumeView.ts, existing CLI command registry and desktop project/session surfaces. Proposed: project/resume/capture and memory receipt renderers. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Create/select a project, state its purpose, attach canonical sources, and distinguish editable outputs from reference material. Multiple conversations share a project through explicit IDs; project selection alone grants no new source access.
2. Implement explicit resume with brief catch-up, show changes and continue options. Resolve current artifact versions and live mission status first; show accepted decisions, new changes, blockers and one relevant next action.
3. Recall for Ryoko shows source/date/scope and stated-versus-inferred or superseded/conflicting information. Open source and compare-current actions use authorized references. No found record is stated plainly rather than invented.
4. Preference correction supports only this response, this project/activity or general default; remember/change/forget actions show pending then acknowledged success. A one-off edit is not silently made permanent. Next relevant output reflects a confirmed correction without rebuilding cached history.
5. Non-Ryoko agents render their own built-in memory operations. Explain explicitly shared project context without exposing another agent’s private memory. Harness outage affects Ryoko recall but does not disable an unrelated specialist’s built-in memory.
6. Capture files/links/text into an unfiled queue with original receipt, annotation and suggested project. Filing and duplicate consolidation are reviewable/reversible; extraction failure keeps the original accessible. Capture does not automatically launch research or trust the content.
7. Later mobile share/voice/watched-folder intake is an adapter over the same Capture contract, not a prerequisite for the first CLI/TUI workflow.

8. Capture includes bounded authorized text/extracted-content indexing with processing status and batch review/filing; approximate-description retrieval returns the original. Provide an inspectable scoped preference list and “what influenced this output” references with remove/ignore-here/correct controls, using actual acknowledgment and version semantics.

**Interface and data contract:** ProjectView, ResumePackage, CaptureView and MemoryResult come from BE07/BE08; current artifact version, pending correction ID, acknowledged memory version and degraded-source status must be visible where material.

**Failure and security boundary:** Prevent stale project selection, wrong-scope corrections, unreachable source links and false deletion acknowledgment. Do not show personal harness records to specialists through cached UI state.

**Focused checks:** Focused: two conflicting project styles; external file edit before resume; unavailable memory; correction affects correct project only; agent A→B→A UI cache isolation; capture indexing/batch review, extraction failure and duplicate annotations; inspect/remove output-influence preference.

**Acceptance and exit:** P01/P02/P03/P06 pass: current project/source version is used, correction is acknowledged and scoped, and resumption starts useful work without rereading full chat.

**Integration and rollback:** Keep prior artifact/source pointers and correction receipts. Disable new capture adapters independently; do not lose unfiled originals or migrate private memory as a UI rollback.

**Chronicle and Git checkpoint:** append a `FE02` entry to root [buildjournal.md](buildjournal.md); branch `build/fe02-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe03"></a>

### FE03 Deliver complete files precise revisions templates and branches

**Status:** planned. **Objective and value:** Deliver F10/F11/F32 and the first reusable output slice.

**Prerequisites:** BE06/BE07/BE10 and FE02. Markdown first; each further format needs its own validator.

**Files and ownership:** Existing: CLI file/link output, ui-tui attachment rendering, apps/desktop/src/app/artifacts/, apps/desktop/src/app/right-sidebar/files/, web/src/pages/FilesPage.tsx. Proposed: artifact manifest/revision/template commands and minimal version comparison projection. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Present each output with format, complete-file access, version, producing mission, validation state and related derivatives. Verify the intended viewer can retrieve it before showing ready.
2. Collect a targeted revision against an exact base version and section/selection scope. Show preserved/changed sections and invalidated derivatives; a new instruction does not authorize unrelated rewrites.
3. Handle concurrent external edits with compare/branch/rebase choices. Keep accepted baselines immutable and display a meaningful version diff; cancel leaves the original intact.
4. Save an approved example as a template with structure/style/assets/slots and explicit exclusions. Preview on a new topic and let the user lock elements; do not copy incidental names/examples.
5. Offer branches for alternatives and side-by-side comparisons when useful. A chosen branch updates a canonical pointer only after validation; merges show conflicts instead of silently combining incompatible content.
6. Expose failed/unsupported export and partial derivative completion distinctly. A PDF/deck/spreadsheet filename is not evidence of correct layout, formula behavior or completeness.
7. Keep preview sandboxing and download authorization. Sharing/publication is a separate audience-bound effect, not implied by creating or previewing a file.

**Interface and data contract:** ArtifactRef and RevisionRequest {artifact_id, base_version, scope, requested_change, locked_regions, derivative_policy}; TemplateVersion; BranchComparison references immutable versions and validation receipts.

**Failure and security boundary:** No truncated downloads, stale signed access, active HTML escape, path disclosure, false synchronized-output claim or overwrite of unrelated approved content.

**Focused checks:** Focused: complete download digest; one-section revision preserves rest; external edit conflict; derivative stale marker; template new-topic leakage; branch cancel/choose; inaccessible viewer; malicious preview.

**Acceptance and exit:** P04/P05 pass with downloadable versioned Markdown and one approved template; F32 alternative preserves the original and supports evidence-backed selection.

**Integration and rollback:** Revert canonical pointers to prior versions, retain revision history and revoke only newly granted sharing if authorized. UI rollback never deletes content.

**Chronicle and Git checkpoint:** append a `FE03` entry to root [buildjournal.md](buildjournal.md); branch `build/fe03-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe04"></a>

### FE04 Make missions steerable and completion truthful

**Status:** planned. **Objective and value:** Deliver F02 with clear review, cancellation and partial results, using existing goal controls as the starting point.

**Prerequisites:** BE03/BE06/BE09 and FE03.

**Files and ownership:** Existing: hermes_cli/goal_command.py, tui_gateway/methods_session_control.py, ui-tui/src/app/goalStatus.ts, ui-tui/src/app/turnController.ts, ui-tui/src/app/createServerRequestHandler.ts, apps/desktop/src/app/chat/right-rail/. Proposed: mission summary and verification receipt views. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Turn an outcome request into a concise mission brief with deliverables, completion standard, scope and meaningful cost/deadline constraints. Sequential single-agent execution is enough for the first release.
2. Display outcome-level progress and concrete next step; technical trace remains an authorized inspector. Working, waiting-for-you, waiting-for-source, ready-to-review, partial and completed each show the relevant action.
3. Steer updates requirements through a revision-checked command. Pause/cancel have distinct requested/acknowledged states; expose already completed effects and unresolved remote work. Do not hide a late or missed steer.
4. Approvals show exact action/target/content/version/cost/commitment as applicable, expiry and allowed choices. Replayed pending requests retain identity; duplicate click, refresh and conflicting decisions resolve to one backend receipt.
5. Review outputs against the requested manifest and deterministic evidence. Keep ready-to-review distinct from accepted/completed. Delivery failure offers retry-delivery/download, never rerun-entire-mission.
6. On source wait, show condition, last check, expiry and permitted change/cancel actions. Partial completion preserves usable artifacts with exact remaining blockers.
7. Changing profile/connection must not leak pending questions into another foreground session; background terminal transitions update their own cached view without stealing focus.

**Interface and data contract:** MissionSnapshot, ApprovalRequest/ApprovalDecision, Cancellation and DeliveryReceipt from backend. User-language state mapping is specified below; rendering is not authority.

**Failure and security boundary:** No success from stream end, tool API acceptance or spinner stop. No cancellation-as-rollback promise. No stale approval/action substitution or implied approval because a channel lacks controls.

**Focused checks:** Focused: duplicate submit/click; stale approval; terminal event during reconnect; cancellation mid-effect; late steer; partial two-output mission; failed attachment delivery; background session focus isolation.

**Acceptance and exit:** P07 yields two consistent usable outputs; users can correct or stop work and understand exactly what completed, what was delivered and what remains unknown.

**Integration and rollback:** Remove new view affordances without changing pending backend state. Retain inspect/download and read-only mission snapshot for older clients.

**Chronicle and Git checkpoint:** append a `FE04` entry to root [buildjournal.md](buildjournal.md); branch `build/fe04-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe05"></a>

### FE05 Expose connected research living briefs and decisions

**Status:** planned. **Objective and value:** Deliver F07/F08/F09 with verifiable evidence rather than opaque summaries.

**Prerequisites:** BE07/BE09/BE10, FE03; automatic refresh waits for FE06/BE12.

**Files and ownership:** Existing: CLI/TUI source links and artifact output; file/source inspectors. Proposed: research scope, source coverage and comparison commands/view models. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Let the user select two or three relevant source types and show what was searched, which versions are current and where access or freshness is incomplete.
2. Every material claim in a research artifact points to a source/version/range. Separate source statements, inferred interpretation, assumptions and unresolved disagreement.
3. Create a living brief with research question, bounded source list, conclusions and uncertainties. Manual refresh shows changed claims and why a recommendation changed; unrelated conclusions stay stable.
4. Decision aids expose options, criteria, user weights/priorities and measured versus assumed values. Begin with an editable table/artifact; interactive controls are optional only when they clarify a trade-off.
5. Recompute scenario results deterministically; show sensitivity/ranges and conditions under which the preferred option changes. Save an accepted decision with rationale, not a persuasive answer falsely presented as fact.
6. When sources or memory are unavailable, retain the prior version and show partial freshness rather than claiming a complete new review. Broader discovery or monitoring is an explicit scope expansion.

**Interface and data contract:** SourceManifest, EvidenceAnchor, BriefVersion and DecisionRecord from BE10; selection controls submit source grants/refs but cannot grant access themselves.

**Failure and security boundary:** No unsupported citations, stale content labeled current, false numeric precision, automatic monitoring or use of a memory summary instead of current authoritative source.

**Focused checks:** Focused: source unavailable; two versions conflict; controlled change affects one conclusion; changed assumption recomputes result; exact citation opens authorized evidence.

**Acceptance and exit:** P08 and a manual F08 refresh work end to end; users can explain what evidence or assumption changed the answer.

**Integration and rollback:** Keep prior brief/decision versions and source manifests; disable refresh without deleting the last useful artifact.

**Chronicle and Git checkpoint:** append a `FE05` entry to root [buildjournal.md](buildjournal.md); branch `build/fe05-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe06"></a>

### FE06 Make workflows monitors and conditions understandable

**Status:** planned. **Objective and value:** Deliver F19/F20/F21 and scheduled F08 with visible scope and health.

**Prerequisites:** BE11/BE12 plus FE04/FE05.

**Files and ownership:** Existing: hermes_cli cron/skill commands, tui_gateway control contracts, apps/desktop/src/app/cron/, apps/desktop/src/app/capabilities/skills/. Proposed: workflow and monitor summaries over existing commands; no required graphical canvas. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Save an accepted mission as an editable parameterized workflow; show inputs, steps, expected outputs, permissions, version and last results. Start with manual run and inspectable text.
2. Rerun with new inputs and show selected immutable workflow version. Edits create a draft needing validation; revoked versions cannot start work.
3. Monitor setup captures the question, sources, meaningful-change criterion, cadence/trigger, expiry and destination. Show last successful check, next check, error and notification policy.
4. Distinguish a check that found no change from one that failed. Alert previews show relevant evidence and useful/not-useful feedback; batching/digests cannot hide high-importance missed-item metrics.
5. Conditional task review separates the trigger from the proposed action and its exact authorization scope. A meaningful event may request a decision without automatically executing a new effect.
6. Pause/resume/cancel/revise are receipt-backed; schedule timezone and DST behavior are explicit. Deletion, expiry, revoked source and changed conditions leave honest terminal/pending states.
7. Demonstration learning F18 is a later authoring input for this workflow contract, not a reason to build a separate recorder-driven automation engine.

8. Monitor controls include quiet hours, digest policy and snooze with explicit expiry; snooze suppresses delivery according to policy without pretending checks stopped or discarding an important pending change. Persist deduplication and dismissal state so unchanged alerts do not repeatedly resurface.

**Interface and data contract:** WorkflowVersion/WorkflowRun, Monitor, ScheduleSnapshot and Occurrence from BE11/BE12; frontend state includes current version, health and execution/delivery distinction.

**Failure and security boundary:** No silent standing permission, unbounded polling, concealed ongoing cost, duplicate schedule ownership or source-failure-as-no-change. Retry delivery does not rerun workflow.

**Focused checks:** Focused: second varied workflow run; controlled relevant/cosmetic changes; monitor outage; pause and pending trigger race; changed condition invalidates action approval; DST display; quiet-hours/snooze expiry and unchanged-alert dedup; duplicate event.

**Acceptance and exit:** P09/P10 pass and a conditional action stays inside its grant; user can stop work and see whether the last check actually succeeded.

**Integration and rollback:** Pause affected schedules before changing versions or consumer ownership. Preserve monitor baselines and occurrence receipts; do not refire missed effects automatically.

**Chronicle and Git checkpoint:** append a `FE06` entry to root [buildjournal.md](buildjournal.md); branch `build/fe06-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe07"></a>

### FE07 Package data teaching tutoring and creative production

**Status:** planned. **Objective and value:** Cover F12/F14/F15/F18/F28 as focused domains on the shared project/artifact/workflow spine.

**Prerequisites:** BE10/BE11 and FE03/FE06; choose one or two Slice-D packages first, keep others explicitly planned.

**Files and ownership:** Existing: skills/ and optional-skills/ entry points, CLI/TUI attachments, artifact previews. Proposed: domain-specific input/review templates and validation summaries; no domain-specific duplicate task engine. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Data work shows selected dataset/version, transformations, units, missingness and reproducible calculation/formula references. Provide a validated workbook/report and explain unresolved data-quality issues.
2. First F12 controls cover CSV/XLSX upload/version, profile summary, date/currency/null/duplicate/unit assumptions, ambiguous-column mapping, join/aggregation choices and a small chart set. Display the saved transformation recipe and source-row lineage for a selected result, and return a complete workbook or notebook.
3. Lesson production collects audience/objectives/source material/style kit, then produces aligned lesson outline, teaching artifact, exercises and answer key. A targeted edit propagates to dependent materials without changing locked layout.
4. F14 includes learning goal, short diagnostic, hint progression, spaced revisit choice, learner-visible exercise/progress history, saved next step, reset assessment and change difficulty controls. F15 separately shows prerequisite/audience/objective→slide→exercise coverage and estimated pacing for educator review; evaluate tutoring on a held-out transfer exercise.
5. Tutoring separates explanation, practice, hint and assessment. Use verified answers, bounded adaptation from demonstrated performance and inspectable learner preferences; do not infer ability or sensitive traits from sparse behavior.
6. Creative pipelines separate brief, prompt-only preparation, reference selection, generation and revision. A request for prompts only must not launch image/audio/video generation. Track asset rights, references and continuity across outputs.
7. Demonstration authoring presents extracted steps and variable versus locked inputs for correction, then tests on a second example. Do not silently record continuous screen/audio or treat demonstration as permission for future external actions.
8. Each format exposes supported fidelity and validation status. A failed renderer or unavailable generation capability leaves a useful plan/partial artifact without fabricated media.
9. Save only accepted reusable templates/workflows and scoped preferences. No claim that ordinary teaching/correction automatically updates model weights.

10. Creative package review exposes dimensions/duration/audio-format/subtitle-timing checks and explicit generated/supplied/prompt-only/awaiting-production status in the asset manifest; reject inconsistent technical properties even when previews look plausible.

**Interface and data contract:** DomainJob, TemplateVersion and WorkflowVersion; user-facing validation includes formula checks, lesson alignment, answer-key consistency, reference lineage and format render status.

**Failure and security boundary:** No hidden recording, incidental-content reuse, unrequested generation, fabricated calculations/answers, or private learner/source data exported without scope.

**Focused checks:** Focused: CSV/XLSX known-answer joins/formulas/charts and row provenance; tutor reset and held-out transfer exercise; changed lesson section updates answer key only as needed; locked style preserved; prompt-only stage generates zero media; varied demonstration replay; unsupported export honest status.

**Acceptance and exit:** A chosen domain package yields usable validated outputs with lower total review/cleanup effort; all unchosen domain features remain mapped and deferred, not falsely shipped.

**Integration and rollback:** Revert template/workflow version and preserve original data/media. Stop queued generation on cancellation while retaining charged/accepted remote-operation uncertainty.

**Chronicle and Git checkpoint:** append a `FE07` entry to root [buildjournal.md](buildjournal.md); branch `build/fe07-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.



<a id="fe08"></a>

### FE08 Turn communications into reviewed follow-through

**Status:** planned. **Objective and value:** Deliver F13/F22/F23/F24 and daily operations without inventing commitments or sending by implication.

**Prerequisites:** BE06/BE10/BE12 and FE04/FE06.

**Files and ownership:** Existing: gateway messaging controls, source/tool output and mission summaries; proposed meeting outcome, agenda, correspondence and commitment review projections. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. For supplied recordings/transcripts show source timestamps, speaker uncertainty, decisions and candidate commitments. Allow correction before acceptance; recording/transcription scope and retention remain visible.
2. Commitment review identifies owner, exact outcome, due/check date, supporting source and waiting-on state. Only accepted obligations enter the authoritative active list; declining a suggestion does not create work.
3. Provide a weekly review and waiting-on view of accepted/tentative/delegated/completed/no-longer-relevant commitment states. Link duplicates to one authoritative obligation, and require evidence or explicit correction before closing/reopening it.
4. Agenda planning uses current calendar evidence, timezone, travel/buffer constraints and conflict checks. Preview changed events separately from private planning suggestions.
5. Inbox triage begins with one chosen source and explicit threads or bounded time window. Show actionable request, informational, decision-needed and waiting-on categories; group related threads, surface dates and draft rationale, flag any new commitment, and allow edit/approve/defer. Track missed requests and false urgency as release counter-metrics; no unattended blanket replies.
6. Correspondence clearly labels draft, queued, sent/accepted and delivered/unknown. Resolve intended recipients and show exact content before consequential sends; editing a draft does not authorize send.
7. Follow-through shows expected source/response and expiry, updates from actual evidence and avoids duplicate reminders after completion or cancellation. User can narrow or pause the workflow.
8. Briefings summarize genuinely ready/waiting/decision-needed items with links and concise next actions. Avoid activity noise, invented urgency or engagement-driven suggestions.

9. Agenda review offers day/week views separating fixed commitments, flexible work blocks and overflow, with realistic capacity/buffers. A proposed plan is distinct from writing calendar events; impossible workloads expose overflow instead of silently overbooking.

**Interface and data contract:** MeetingOutcome, accepted Commitment and ScheduleSnapshot projections; correspondence references exact draft revision, recipient identity and EffectReceipt/DeliveryReceipt.

**Failure and security boundary:** No speaker guess turned into obligation, wrong recipient, stale meeting time, hidden send, unapproved promise or repeated follow-up after closure.

**Focused checks:** Focused: corrected speaker; declined commitment; inbox missed requests/false urgency; weekly waiting-on review; ambiguous due date; timezone conflict and day/week fixed/flexible/overflow plan; draft edited after approval; send accepted but receipt lost; completed obligation disappears from active briefing.

**Acceptance and exit:** One meeting becomes accurate reviewed obligations/drafts and a useful briefing without false commitments or unrequested communications.

**Integration and rollback:** Pause monitors/follow-ups, preserve accepted records and delivery receipts, and never retract or delete sent communications as an automatic UI rollback.

**Chronicle and Git checkpoint:** append a `FE08` entry to root [buildjournal.md](buildjournal.md); branch `build/fe08-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.



<a id="fe09"></a>

### FE09 Show coding browser and device work with real outcomes

**Status:** planned. **Objective and value:** Deliver F25/F26/F27 with clear execution placement, review and effect evidence.

**Prerequisites:** BE05/BE06/BE09/BE10/BE13 and FE04.

**Files and ownership:** Existing: terminal/browser controls, apps/desktop/src/app/right-sidebar/review/, apps/desktop/src/app/right-sidebar/terminal/, tui_gateway/methods_browser*.py. Proposed: execution placement, staged change manifest and effect-status projections. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Coding review identifies repository, baseline/current revision, worktree, scope, changed files and tests actually run. Separate prepare/edit, commit/push, PR/merge and deployment states and permissions.
2. Display staged output/diff before promotion when required; preserve accepted user edits and show conflicts. A passing focused test is not a full-suite claim.
3. Browser task controls show current page/form state, intended submit and exact consequential details. Completion requires visible/provider evidence; prepared form is not a confirmed booking/purchase/send.
4. Device routing shows which authenticated machine/backend runs work and why placement matters. Allow selection among actual capabilities, not fabricated availability. If the host disconnects, preserve pending/unknown state and explain the smallest remedy.
5. For the first F27 demonstration show two explicitly registered bounded services, live health/capability, transfer size and processing/storage location. A transcription→document-processing pipeline returns traceable artifacts; loss of one endpoint exposes pending/partial state and permitted recovery without asking the user to shuttle files or silently substituting a cloud service.
6. Current screen observations have capture time/scope; stale coordinates require refresh. Sensitive auth/permission steps use supported secure handoff. Stop/takeover controls must be real backend/executor signals.
7. No silent move from local/private execution to cloud, and no retry of an entire mission to repair a lost result message.

**Interface and data contract:** ExecutorCapabilities, WorkspaceManifest, validation receipts and EffectReceipt from backend; view distinguishes unavailable executor, rejected scope, queued job and interrupted transport.

**Failure and security boundary:** No false code-tested/deployed claim, wrong computer, secret in transcript, stale screen action or external submit without the required grant.

**Focused checks:** Focused: externally edited repo; staged merge conflict; unavailable target; two-service transfer digest/size/location and one-endpoint loss; disconnected executor; browser timeout after submit; hidden approval route; focused-versus-full validation labels.

**Acceptance and exit:** One bounded coding or browser workflow reports the actual final state and preserves reviewability, placement and unresolved effects.

**Integration and rollback:** Stop new admission and leave artifacts/diffs readable. Rollback code/deployment is a separate authorized effect, not an automatic consequence of closing a view.

**Chronicle and Git checkpoint:** append a `FE09` entry to root [buildjournal.md](buildjournal.md); branch `build/fe09-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.


<a id="fe10"></a>

### FE10 Expose specialists teams voice screen and handoff

**Status:** planned. **Objective and value:** Deliver F16/F17/F29/F30/F31 on the same runtime contracts, without duplicate memory or mission authority.

**Prerequisites:** BE08/BE13 plus FE04/FE09. OpenDots renderers remain FE14; this phase defines and exposes current supported controls first.

**Files and ownership:** Existing: tui_gateway/methods_subagents.py, tui_gateway/methods_voice.py, ui-tui/src/app/agentRoster.ts, ui-tui/src/app/delegationStore.ts, apps/desktop/src/app/agents/, existing gateway adapters. Proposed: typed specialist/child summaries and channel handoff controls. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Specialist selection shows responsibility, methods, permitted tools, own built-in memory and output contract. Cosmetic persona changes alone are not a feature. Ryoko’s personal harness is never offered as shared team context.
2. Team view displays concrete assignments, dependencies, owners, budget, status and synthesized result. It must show blocked/failed children and preserve the parent’s responsibility for coherence.
3. Explicitly share project artifact versions/task briefs with a child; show what context is shared when relevant. Agent-local private memories remain separate, including between specialists.
4. Voice reflects transcript uncertainty, interruption/correction and confirmation state. Stopping speech, disconnecting a call and cancelling accepted work are distinct choices; no misheard high-impact action silently executes.
5. Initial voice controls are push-to-talk, streaming text feedback, interruptible speech and explicit discussion→task transition. Select supported local/remote STT/TTS and disclose processing location when material; hands-free activation and custom voice remain later separately authorized choices. Confirm ambiguous decisive names/numbers rather than trusting a transcript blindly.
6. Screen controls distinguish inspect, guide and act, identify snapshot versus live window/desktop, support bounded-region screenshot annotations, and expose a stop button for the first supported app workflow. Re-observe after layout change; unreadable text prompts clarification rather than invented hidden UI state.
7. Screen companion starts with explicit snapshots or chosen app/window, not continuous capture. Observation scope, freshness, takeover and consequential submit gates remain clear.
8. Handoff to another device/channel resumes the same mission/project/artifact versions through verified identity mappings. A reconnect cannot recreate the mission or lose pending approvals.
9. Measure whether specialist/team/voice/screen actually saves effort over the single-agent/manual baseline. Default to the simpler path when added coordination or cost wins no value.

**Interface and data contract:** SpecialistManifest, Delegation, IdentityBinding and channel capabilities; child state is authoritative snapshot plus events, with queued steer distinct from delivered steer.

**Failure and security boundary:** No personal-memory leak via roster/tail/log, phantom child progress, duplicate work, implicit recording, stale transport controls or 90-second client deadline cancelling durable work.

**Focused checks:** Focused: named specialist persists its own memory; ephemeral child isolation; copied identity grant denial; team partial failure; push-to-talk/streaming transcript/local-remote speech scope; voice barge-in/hangup and discuss-versus-task; screenshot annotation and inspect/guide/act mode; stale screenshot; cross-device duplicate event; reconnect pending approval.

**Acceptance and exit:** One real cross-surface handoff and bounded specialist task preserve scope and completion truth; teams are enabled only with measured value.

**Integration and rollback:** Fall back to current single-agent controls, keep children tracked, revoke channel access if authorized and leave accepted missions owned by Hermes until resolved.

**Chronicle and Git checkpoint:** append a `FE10` entry to root [buildjournal.md](buildjournal.md); branch `build/fe10-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.



<a id="fe11"></a>

### FE11 Provide useful status setup repair and opportunity controls

**Status:** planned. **Objective and value:** Finish F33/F34 and on-demand F35 while making U28/U29/U34 operationally usable.

**Prerequisites:** BE07/BE12/BE14 and preceding feature contracts.

**Files and ownership:** Existing: hermes_cli/doctor*.py, gateway/status.py, apps/desktop/src/app/command-center/, settings and capabilities, CLI/TUI status. Proposed: coherent read-only status/repair projections and scoped opportunity request workflow. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Begin with a compact project status view of ready outputs, pending decisions, waiting sources and unhealthy connections. Expand to a briefing/command center only when it reduces tracking effort; no new dashboard redesign is required.
2. Provide why-waiting, who-owns, budget-use, did-action-happen and was-delivered inspectors. Redact private payloads and expose detailed traces only to the right operator.
3. Repair actions preview affected IDs and intended state: retry delivery, reconcile effect, restore checkpoint or rebuild index. Confirm required changes, submit to backend broker and show receipt; no mark-everything-successful shortcut.
4. Privacy controls show per-store export/delete/retention capability and actual acknowledgments/limitations. Unsupported harness deletion is not reported as completed. Distinguish hiding a record from deleting all copies.
5. On-demand opportunity discovery takes a selected project set and current evidence, proposes a concrete reuse/collaboration/next step with benefit/effort, and lets the user dismiss without creating a task. Excluded/private projects stay excluded.
6. Relevant setup suggestions use a real intended workflow and verified capability; broad marketplace/persona expansion is deferred. Notification and recurring cost controls remain inspectable.

7. Use four bounded status queues: ready to review, waiting for user/source, active and upcoming. Persist dismissal/deduplication with source revision so unchanged suggestions do not reappear; a materially changed item may return with the reason. Setup starts with a small supported capability catalog and two actual end-to-end integration flows, each proving a useful operation and repair/disconnect behavior. Opportunity candidates have saved/dismissed/accepted states; dismissal alone creates no task and suppresses unchanged resurfacing.

**Interface and data contract:** RepairPlan, AuditProjection, DeletionManifest and OpportunityCandidate {authorized_project_refs, evidence_refs, suggested_action, benefit, effort, confidence, disposition}; no new work until accepted when authority is required.

**Failure and security boundary:** No misleading green status, automatic sensitive-data exports, internal raw trace leakage, superficial opportunity spam or repairs that retarget another profile.

**Focused checks:** Focused: orphan/unknown/delivery-failed diagnosis; repair preview conflict; redaction; partial deletion acknowledgment; selected-project opportunity evidence; dismiss creates no task; unhealthy connection probe; two real supported setup flows; persisted queue/opportunity dismissal and changed-source resurfacing.

**Acceptance and exit:** Users can take one useful next action from truthful status and safely recover a supported failure; on-demand opportunities cite real authorized work.

**Integration and rollback:** Keep read-only inspection and existing project controls available; stop optional suggestions rather than modifying active commitments during rollback.

**Chronicle and Git checkpoint:** append a `FE11` entry to root [buildjournal.md](buildjournal.md); branch `build/fe11-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe12"></a>

### FE12 Explain and control LAYA decisions without exposing authority

**Status:** planned. **Objective and value:** Deliver P11–P16 and the user-facing parts of DP01–DP16 only as their backend gates pass.

**Prerequisites:** BE15/BE16; FE06 monitors, FE04 mission verification and FE10 channels as applicable.

**Files and ownership:** Existing: activity/status and approval projections; proposed decision explanation, feedback and per-point mode controls using BE15 receipts. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Expose why a message was routed, work held, review triggered or alert ranked in plain language linked to the recorded decision. Do not expose raw private state packets or fabricate model reasoning.
2. Useful/not-useful feedback and correction annotate independent labels with scope. A user override can change optional routing behavior within policy but cannot override a deterministic required confirmation or denied capability.
3. Ambient group-chat triage is per-channel opt-in; direct questions/DMs remain protected. Skipped/held items are inspectable and recoverable.
4. Done-claim verification shows concrete missing evidence or next action, not an opaque confidence badge. Tests/artifacts/receipts remain authoritative.
5. Reduced approval prompts require a visible proven-safe action-class setting and recorded backend gate. Model confidence alone never expands authority.
6. Tool planning should mostly be invisible: simple questions respond promptly, but an authorized missing tool is recovered automatically. Show a real recovery/blocker only when it affects work; never tell the user a hidden tool does not exist.
7. When the Jetson is unavailable show fallback only if material to latency/capability; keep honest system health and per-point off/shadow/advisory/enforce controls in the operator view.

**Interface and data contract:** DecisionReceipt safe projection {point, plain_reason, route, fallback, model_contract_version, override_action?, evidence_refs}; full distribution/calibration belongs in authorized diagnostics, not a generic truth score.

**Failure and security boundary:** No permission-by-confidence, raw personal packet leak, synthetic explanation, hidden missed direct request or broad fallback to an unapproved external judge.

**Focused checks:** Focused: decision explanation matches receipt; monitor feedback; ambient/direct distinction; missing done evidence; safe-class toggle cannot bypass floor; no-tools miss recovery; Jetson outage fallback.

**Acceptance and exit:** P11–P16 each have a backend evaluation receipt and truthful user controls. Improvements are reported as measured outcomes, not promised speed/safety percentages.

**Integration and rollback:** Return one point to shadow/off through backend policy, preserve feedback/receipts and reinstate its prior safe behavior without replaying work.

**Chronicle and Git checkpoint:** append a `FE12` entry to root [buildjournal.md](buildjournal.md); branch `build/fe12-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe13"></a>

### FE13 Validate complete user journeys and release behavior

**Status:** planned. **Objective and value:** Consolidate frontend/product acceptance after focused phase checks. Do not spend hours repeatedly testing a documentation-only change.

**Prerequisites:** The candidate assembled in the first part of BE18 for the selected slice, plus mandatory authority/memory/recovery gates and enabled FE phases. FE13 returns journey receipts to BE18 for final sign-off; it does not depend on BE18 already being complete.

**Files and ownership:** Existing: scripts/run_tests.sh, tests/tui_gateway/, apps/shared tests, ui-tui vitest, web vitest, desktop scripts and tests-js. Proposed: paired journey fixtures and cross-repo contract suite. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Run generated-contract freshness, relevant Python gateway/control tests and JS typecheck/test/lint via declared workspace scripts. For final implementation release run the required aggregate suites once on the final source state; clearly list blocked/not-run platforms and live services.
2. Exercise keyboard-only and narrow terminal/window layouts, long histories, reconnect, error recovery, duplicate click, interrupted upload/setup, stale async response, Back/Close/Cancel and focus ownership. Accessible status cannot rely only on color.
3. Run the six source journeys: lesson package/revision/resume; research→plan→branch→refresh; meeting→accepted commitments/drafts; demonstrated creative workflow with prompt-only stage; cross-device work/voice steering; scoped opportunity discovery.
4. Run the proposed ten-task pilot with matched current-method and candidate inputs: two resumptions, two deliverables, one revision, one cross-source question, one repeat workflow, one monitor, one memory correction, one setup. Count all failures and user review/cleanup effort.
5. Include conflicting projects, stale preference, unavailable source/harness, external artifact edit, partial mission, failed delivery, unknown effect and cancelled work. Repeat variable model tasks and report uncertainty.
6. Check actual artifact openability/viewer access and receipt-backed completion. UI screenshots prove layout only; they do not prove server authorization, effect completion or delivery.
7. Record release scope, known limitations, exact commits/config/contracts and rollback evidence. Do not label all 35 capabilities shipped when only one slice is enabled.

**Interface and data contract:** JourneyReceipt {fixture, producer_consumer_versions, expected_outcome, observed_artifacts, authority_checks, completion_delivery_state, user_effort, latency_cost, result, limitations}.

**Failure and security boundary:** No test-count vanity or benchmark claims without comparable evidence. Critical cross-agent memory or effect safety failure blocks release regardless of visual polish.

**Focused checks:** Use the consolidated matrix below and backend gates. This plan publication itself receives Markdown, coverage, path/link and docs-only diff checks; no application tests claimed.

**Acceptance and exit:** The selected experience works a second time under failure and preserves user control; all release claims have accessible evidence and relevant counter-metrics.

**Integration and rollback:** Revert UI canaries/flags, retain old compatible clients and authoritative server state; follow backend rollback for migrations, never erase pending work to hide errors.

**Chronicle and Git checkpoint:** append a `FE13` entry to root [buildjournal.md](buildjournal.md); branch `build/fe13-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

<a id="fe14"></a>

### FE14 Hand the stable runtime contract to the separate Dots fork

**Status:** planned. **Objective and value:** Prepare and later integrate OpenDots after the custom Hermes runtime is usable and safe. Full Dots implementation does not belong in this repository.

**Prerequisites:** BE02 early contract, BE05/BE06 authority/effects, BE07/BE08/BE09 Slice A/B, FE13 and BE18 production readiness. Follow Integration_Handoff.md in ryoko-dots.

**Files and ownership:** Future ryoko-dots owners: src/server/platform.ts, dot-agent.ts, platform-config.ts, headless.ts, runner.ts, store.ts, workspace.ts, learning.ts, voice.ts, slack-channel.ts and its client review/task/page surfaces. These are not files to add to ryoko-agent. All paths labeled proposed are design targets, not existing implemented features; choose the smallest existing topical seam before adding a module.

**Implementation sequence**

1. Use OD00 fixture/read-only proof to validate IdentityBinding, command receipts, snapshots and replay early. Production write-capable cutover waits for the Hermes authority/effect/mission gates.
2. Replace DotAgent’s inner generative/tool loop at shared Platform and Slack factory seams with a Hermes-backed AG-UI adapter. Merely changing OPENAI_BASE_URL or exposing Hermes as a tool leaves competing orchestration and is not the chosen integration.
3. Keep chat/Spaces/pages/review/computer/voice/Slack surfaces as useful projections. Register one authoritative store per native artifact; page revisions and manual edits remain conflict-checked.
4. Remove global preference injection for migrated sessions. Map each non-primary Dot to a stable Hermes specialist and isolated built-in memory; migrate legacy preferences only to explicitly chosen permitted destinations. Reconcile automatic learning as a separate authorized skill/evidence pipeline.
5. Migrate scheduler ownership once with stable occurrence/import IDs and no overlap. Web/Slack/voice use one mission identity; 90-second request/call limits do not terminate accepted durable missions. Detach and cancel remain distinct.
6. Carry exact approval binding and unresolved effects across reconnect; AG-UI finish/stop is mapped to runtime semantics, never assumed successful completion. A channel without secure review links to an authenticated review surface rather than granting permission.
7. Cut over by runtime_owner canary. Rollback stops new admission and retains Hermes ownership of already accepted missions until resolved; never rerun accepted work through the old DotAgent loop.
8. Complete Dots OD01–OD04 in its own repo, with its own source commits, CI, visual/connected-service tests and authorized deployment. Keep cross-repo contract fixtures synchronized.

**Interface and data contract:** The exact shared contracts are BE02/BE06/BE07/BE08 plus the Dots handoff. RuntimeCapabilities negotiates supported versions and operations; service and human actor authentication remain separate.

**Failure and security boundary:** No dual agent loops, dual schedulers, shared personal-memory list, direct browser-to-Jetson path or implicit credential/persistent-access creation.

**Focused checks:** Future: duplicate web/Slack/voice submission, restart/cursor gap, pending approval/revocation, voice hangup, unknown effect, delivery failure, native page conflict, migrated memory isolation, canary rollback.

**Acceptance and exit:** A separate Dots implementation consumes the stable Hermes contract and demonstrates one owner per mission/effect/schedule. This documentation commit only records the handoff.

**Integration and rollback:** Follow Dots handoff runbook and BE18. Preserve mappings/journals/artifacts and render a read-only recovery state if necessary; do not restart work under the legacy loop.

**Chronicle and Git checkpoint:** append a `FE14` entry to root [buildjournal.md](buildjournal.md); branch `build/fe14-<topic>`. Record before/after behavior, migration/flag state, exact validation receipts and unresolved dependencies using the working method above. Cross-link the matching BE/FE consumer changes; no phase is complete from code presence alone.

## Complete product coverage F01 to F35

Source: product §5; dependencies in §10.1. Release letters refer to the shared slices above. “Experiment” and “selected” are deliberate scope gates, not omissions. Ryoko personal recall is harness-backed; specialist context is individual built-in memory plus granted artifacts.

| ID and capability | Frontend phase | Backend dependency | Release | Acceptance demonstration |
|---|---|---|---|---|
| F01 Persistent project workspaces | FE02 | BE02, BE07 | A | Correct project identity and canonical source version on next conversation |
| F02 Goal-to-deliverable missions | FE04 | BE03, BE06, BE09 | B | Two linked complete outputs with acceptance and honest partial/delivery state |
| F03 Project resumption and catch-up | FE02 | BE02, BE07, BE08 | A | Resume current work after interruption with correct next action and versions |
| F04 Universal capture inbox | FE02 | BE07, BE14 | D | Retrieve approximate-description capture and original even after extraction failure |
| F05 Personal context and decision recall | FE02 | BE08 | A | Ryoko recalls supported dated decisions; other agents only own built-in context |
| F06 Teach and correct personal preferences | FE02 | BE08 | A | Acknowledged scoped correction changes relevant output only |
| F07 Connected knowledge investigator | FE05 | BE05, BE07, BE10 | B | Two authorized source types and verifiable claims/current versions |
| F08 Living research briefs | FE05, FE06 | BE10, BE12 | C | Controlled update changes affected claim, preserves unrelated conclusions |
| F09 Decision and scenario lab | FE05 | BE09, BE10 | B optional | Decisive assumption changes result coherently with explicit uncertainty |
| F10 Deliverable studio and precise revisions | FE03 | BE06, BE07, BE10 | A then B | Complete valid file; targeted edit preserves unrelated approved content |
| F11 Reusable templates and style kits | FE03 | BE07, BE11 | A | Three new outputs reuse structure/style without incidental content |
| F12 Data analyst and spreadsheet assistant | FE07 | BE10 | D selected | Reproducible source-linked calculations and usable workbook/report |
| F13 Meeting and lecture outcome assistant | FE08 | BE10, BE12 | E | Timestamped outcomes with corrected speakers and human-accepted commitments |
| F14 Adaptive tutor and practice coach | FE07 | BE10, BE11 | G experiment | Verified exercises and useful adaptation without unsupported learner inference |
| F15 Course and lesson production assistant | FE07 | BE10, BE11 | D selected | Aligned lesson, teaching materials, exercises and answer key |
| F16 Natural voice conversation and action | FE10 | BE03, BE13 | F | Interrupt speech and continue same mission without duplicate actions |
| F17 Screen-aware companion | FE10 | BE05, BE13 | F selected | Explicit fresh screen scope and real takeover/stop controls |
| F18 Learn a workflow from a demonstration | FE07 | BE11 | G after F19 | Editable demonstrated procedure works on new input within grants |
| F19 Personal workflow composer | FE06 | BE11 | C | Parameterized manual workflow works a second time |
| F20 Meaningful monitors and alerts | FE06 | BE12 | C | Relevant changes notify; cosmetic noise does not; outage health visible |
| F21 Conditional tasks and follow-through | FE06 | BE06, BE12 | C after F20 | One intended trigger/action with current bounded authorization |
| F22 Agenda and calendar planning | FE08 | BE06, BE12 | E | Feasible current-timezone agenda and separately approved calendar changes |
| F23 Inbox triage and correspondence | FE08 | BE06, BE12 | E | Correct recipient/content; draft/send/delivery states distinct |
| F24 Commitments and follow-up manager | FE08 | BE12 | E | Source-backed accepted obligation remains current without false reminders |
| F25 Browser task completion concierge | FE09 | BE05, BE06, BE10 | G experiment | Supported browser task has verified final/unknown outcome, not merely prepared form |
| F26 Coding and application change missions | FE09 | BE05, BE09, BE10 | D selected | Reviewable scoped changes and actual relevant validation receipts |
| F27 Connected-device task routing | FE09 | BE13 | G experiment | Correct executor placement with truthful disconnect/recovery |
| F28 Creative media production pipelines | FE07 | BE10, BE11 | D selected | Complete coherent media package; prompt-only stage generates no media |
| F29 Named specialists with distinct responsibilities | FE10 | BE08, BE13 | F | Distinct useful responsibility and isolated built-in memory |
| F30 Visible specialist teams | FE10 | BE03, BE13 | G experiment | Coherent bounded team outperforms simpler baseline enough to justify cost |
| F31 Cross-device and cross-channel handoff | FE10, FE14 | BE02, BE06, BE13 | F | Same mission on another verified surface with no duplicate effects |
| F32 Alternative branches and version comparison | FE03 | BE07 | A/B where useful | Compare/choose alternatives without losing baseline or hidden overwrite |
| F33 Personal command center and briefings | FE11 | BE12, BE14 | E; narrow status early | One accurate useful ready/waiting/decision view with low noise |
| F34 Guided capability and integration setup | FE01, FE11 | BE01, BE05, BE14 | A minimum | Actual capability works or explains exact repair/unsupported state |
| F35 Cross-project connection and opportunity finder | FE11 | BE07, BE08, BE10 | G on demand | Evidence-backed useful opportunity within selected projects; dismiss creates no task |

## Product starter tickets P01 to P16

Each starter ticket is a bounded slice inside its owning phase, not an assertion of work completed.

| Ticket | Requested outcome | Source completion evidence | Phase |
|---|---|---|---|
| P01 | Create/select a project and attach a canonical source artifact | The next conversation uses the selected project and correct artifact version | FE02 / BE07 |
| P02 | Retrieve a project context package through the actual memory MCP server | The answer uses relevant records with source/scope information and handles unavailability honestly | FE02 / BE08 |
| P03 | Correct one project preference through the harness | The next relevant output changes, and an unrelated project does not | FE02 / BE08 |
| P04 | Create one complete Markdown deliverable with version history | The user can download, inspect, revise, and recover the prior version | FE03 / BE07 |
| P05 | Save an approved output as a reusable structural template | A new topic follows the structure without copying incidental content | FE03 / BE11 |
| P06 | Resume a project with a compact catch-up and next action | The user continues the current work without rereading the full chat | FE02 / BE07–BE08 |
| P07 | Run a bounded mission producing two linked artifacts | Both outputs are present, consistent, and clearly marked ready or blocked | FE04 / BE09 |
| P08 | Search two authorized source types and cite the answer | Material claims link to the correct current sources | FE05 / BE10 |
| P09 | Save the successful mission as a parameterized manual workflow | A second run with new input succeeds without rewriting the instructions | FE06 / BE11 |
| P10 | Monitor one selected source for a meaningful change | Controlled relevant changes notify; cosmetic changes do not | FE06 / BE12 |
| P11 | Monitor alerts use LAYA importance scoring with “useful / not useful” feedback | Useful-alert precision rises against the current scorer on a labeled week of alerts, with no missed high-importance items in the test set | FE12 / BE16 optional |
| P12 | “Why did you do that?” shows a plain-language reason for routing, skip and gate decisions | A user can trace a skipped message or a held outbound message to its decision and override it | FE12 / BE15–BE16 optional |
| P13 | Done-claim verification on missions and coding tasks | Fewer premature completions on a fixture set, and no increase in stuck missions | FE12 / BE09–BE16 optional |
| P14 | Group-chat ambient triage with per-channel opt-in | Fewer unwanted replies and no ignored direct questions in the pilot | FE12 / BE16 optional |
| P15 | Approval prompts are reduced for one proven-safe action class, with a visible setting | Prompt count drops on benign fixtures and red-team recall is unchanged | FE12 / BE16 optional Guard |
| P16 | Simple questions are answered quickly without loading the tool catalog; tool-heavy tasks start with the right tools | On a paired pilot, faster first response on simple requests, and no task where a needed tool was unavailable without automatic recovery | FE12 / BE16 initial candidate |

## User language and state contract

| State shown | Authoritative meaning | Appropriate control |
|---|---|---|
| Ready | Valid scope/input and admission possible | Start or change requirements |
| Working | A specific admitted step is active | Inspect, steer, pause or request cancel |
| Waiting for you | Exact approval/question remains open | Answer, deny, defer; show expiry/scope |
| Waiting for a source | External event/service/input is pending | Inspect condition/health, change scope, expire or cancel |
| Ready to review | Usable validated artifact exists | Open, compare, revise or accept |
| Completed | Agreed execution criteria and applicable delivery confirmed | Reuse or begin explicitly chosen next work |
| Partially completed | Some outputs usable; remainder blocked | Use finished work and resolve the exact blocker |
| Paused / cancelled | Further work stopped as acknowledged | Inspect committed/unknown effects; resume only where supported |
| Reconnecting / stale | Transport/view is not current | Reconnect or resync; do not imply work stopped |
| Delivery failed / unknown | Execution may be complete, delivery is not confirmed | Download or retry delivery only; reconcile ambiguity |

Keep raw technical details in authorized inspection. Do not manufacture hidden reasoning as a “why” explanation; show observable decision/evidence. A remembered default is overridden by current task instructions, not an authorization source.

## Consolidated frontend and product validation

Phase checks are small. At the final implementation release, run the relevant aggregate commands declared in the current manifests, including generated-contract freshness. Baseline-supported examples:

```bash
scripts/run_tests.sh tests/tui_gateway/contracts/test_generated.py tests/tui_gateway/test_tui_gateway_event_replay.py
npm run --workspace ui-tui check
npm run --workspace web check
```

Use the actual desktop/shared/test workspace scripts when those consumers change; do not invent a script or run an irrelevant full UI build for planning. Prepare dependencies through current repo instructions. Full required project suites belong at the final code release, with affected tests rerun after fixes. No application/runtime tests were run for these Markdown plans.

| Journey or failure | Required evidence |
|---|---|
| Project continuity | Two projects with conflicting styles; correct current artifact; stale preference corrected; unavailable harness reported; specialist own-memory unaffected |
| Produce and revise | Complete linked files; required sections; rendered/openable formats; target edit preserves unrelated content; derivative consistency; prior version recoverable |
| Lesson journey | Course/template + two selected sources → outline/teaching artifact/exercises/answer key → precise revision → next-day resume |
| Research journey | Current repo/docs → cited facts/proposals/questions → plan/backlog → alternative branch → manual refresh → separately requested monitor |
| Meeting journey | Supplied media/transcript → speaker correction → accepted commitments → chosen-recipient drafts → reviewed agenda → expected-source condition |
| Creative teach-once | Approved reference → variable/locked steps → prompt-only preparation produces no images → varied rerun → one-asset revision |
| Device/channel journey | Declared executor → same mission on second client → voice steering → disconnect/reconnect → actual output; no duplicate or silent cloud move |
| Opportunity journey | Selected projects → source-backed concrete candidate → user dismisses without new task; excluded project never appears |
| Interaction reliability | Double-click, reopen, Back/Close/Cancel, stale response, profile switch, reconnect, interrupted upload/setup, long history and background activity |
| Approval/effect truth | Changed recipient/amount/file/version invalidates approval; cancel after dispatch retains unknown effect; lost delivery never reruns mission |
| Accessibility/performance | Keyboard focus, narrow terminal/window, contrast/non-color status, long transcript responsiveness; background events never steal focus |
| LAYA optional | Receipt-backed reason/feedback; direct-message protection; safe-class setting; missing tool recovery; unavailable Jetson fallback; deterministic floor unchanged |
| Dots future | One loop/scheduler/mission authority; durable replay; web/Slack/voice actor bindings; page conflict and memory migration; canary rollback |

Measure total user time, review/cleanup, accepted output quality, source accuracy, relevant alerts/missed changes, repeated corrections, cost and latency. Team/tool/file counts and chat engagement are not success measures. Record failures and variance from repeated paired trials. A suggested ten-task pilot is two resumptions, two deliverables, a revision, cross-source question, repeat workflow, monitor, memory correction and setup; sample size is not a reliability claim.

Feature-ready checklist: discoverable; matches its promise; correctable; stoppable/narrowable; useful when partially blocked; ongoing cost visible when material; works a second time. Acceptance never rests on a screenshot alone.

## Engineering and LAYA cross-reference

All U01–U41 and DP01–DP16 are individually mapped in [BackEnd_BuildPlan.md](BackEnd_BuildPlan.md). FE00/FE14 own the consumer boundary; FE12 owns optional decision explanations and control. Backend phase ownership is listed per F row above, so product features do not silently become UI-only promises.

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
