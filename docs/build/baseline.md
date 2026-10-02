# BE00: reproducible backend evidence baseline

Date: 2 October 2026. Scope: inventory and a reproducible, bounded fixture baseline for
[BE00](../../BackEnd_BuildPlan.md#be00), engineering T01, U31/U33/U35. This is an
implementation aid, not a replacement for the [backend plan](../../BackEnd_BuildPlan.md),
[frontend plan](../../FrontEnd_BuildPlan.md), or [build journal](../../buildjournal.md).

## Evidence vocabulary and limits

- **Observed:** verified by reading the named file at the recorded checkout. Static code
  inspection does not prove runtime behavior, security, or deployment availability
- **Measured:** an executed command with source/config identity, exit status and retained
  output. Only the execution receipts below may establish this category
- **Proposed:** future implementation, fixture extension, rollout choice or target
- **Blocked / not run:** explicit absence of evidence; never count either as a pass

This inventory implements no memory isolation, durable command API, effects broker or LAYA
service. It does not certify hostile-tenant isolation, an external memory harness, an OS
sandbox, or a production deployment. A passing inherited fixture demonstrates its tested
contract only. No live provider cost, latency, task-success or recall improvement is claimed.

## 1. Source and dependency identity

Observed inventory base: `4b7268c69f72c3fa5d2d056a3bbce9a3b65d94cd`, the planning commit in
`TianJieHeng/ryoko-agent`. The plan separately records runtime review base
`b78931e3b0959c42dca7400c78a4dffd1bb48575`. Do not substitute the older supplied Hermes
`0.21.1` / `cbd03e6e4ca143c1d5c2db881320afb85783c30b` analysis for this checkout. The
runtime base and inventory base are distinct identifiers; a shallow checkout may not contain
both objects. Reproduction must record its actual HEAD and dirty diff, not infer ancestry.

Observed manifest SHA-256 values at the inventory base:

| Input | SHA-256 |
|---|---|
| `pyproject.toml` | `d09cd0a47109e5abb9abeb326224208b857faa0d1e921c515d7f0bc6031d2d73` |
| `uv.lock` | `de17d70bc999fb0f8fe50dbf8590ac979f43b5a84cd8320caeac1c695ce63c01` |
| `package.json` | `07122ef9f0c719603efb4ed473310d5c350c86dfa3f3496943df125a7c9d3f9b` |
| `package-lock.json` | `9934677654ce672900b0946898cc28c73671f75896862b5768c24cca84776ccd` |
| `.python-version` | `a876e0b10411037a012498b9fe18d9bc1df32ed8b722a13564dc944ddcfd9135` |
| `BackEnd_BuildPlan.md` | `dd260a93caf4837cad72309cd8789327d9942ba0147261c2a6ca59104fd02919` |
| `FrontEnd_BuildPlan.md` | `229c70e81f96d55596883eac22c58c4d14291e13f959fa3991664729ef2bf275` |

These are input checksums, not a claim of installed dependency equivalence. Recompute after
any edit. Private requirement-source checksums and U/F/DP/T/P coverage remain in the backend
plan's input manifest; the private source documents are not copied into the repository.

### Actual requirements and version resolution

| Observed source | Meaning |
|---|---|
| `pyproject.toml`, `.python-version`, `CONTRIBUTING.md` | Development/runtime target is Python 3.14. The packaging range `>=3.11,<3.15` permits old installations to reach the updater; it does not certify agent execution on Python 3.11–3.13 |
| `uv.lock` | Locked resolution supports Python >=3.14; lock format version 1/revision 3; 14-day core quarantine is represented with explicit package exceptions |
| `pyproject.toml` | Core requirements include exact pins and reviewed bounded exceptions; optional capabilities use declared extras; `dev` and `test` groups supply the independent test environment |
| `package.json` | Node engines: `^22.22.0 || ^24.11.0 || >=26.0.0`; npm: `<11.10.0 || >=11.17.0`. Workspace manifests may impose further requirements |
| `hermes_cli/version_info.py::get_version_info` | Packaged install stamp first, live Git second, explicit unknown last. Record `get_code_identity()`/version provenance when the prepared runtime can run safely |
| Python package version `0.0.0`; root npm package version `1.0.0` | Packaging values, not verified runtime release identities |
| `pyproject.toml` console scripts | `hermes = hermes_cli.main:main`; `hermes-agent = agent.legacy_cli:main` |

Use PM for application/test dependencies. Do not mutate its environments with raw `pip` or
`uv`. Read the current [developer workflow](../../website/docs/reference/package-management.md#developer-workflow)
and [contribution guide](../../CONTRIBUTING.md) before setup. Lock modifications require their
own reviewed change; BE00 is not permission to update all dependencies.

### Backend inventory, not deployment certification

- `hermes_cli/config_defaults.py` selects `terminal.backend: local` by default
- `tools/terminal_tool_backends.py` lists local, Docker, Singularity, Modal, Daytona,
  Vercel Sandbox and SSH built-ins, plus plugin resolution. Presence in a registry is not
  evidence that any backend is installed, authorized, reachable or isolated here
- `hermes_state.py` and topical siblings own SQLite-backed session state; projects already
  have per-profile storage in `hermes_cli/projects_db.py`
- `providers/base.py::ProviderProfile` and `agent/transports/base.py::ProviderTransport`
  are inference seams. `agent/provider_base.py` is not the complete inference interface
- `tools/memory_tool.py::get_memory_dir()` resolves `get_hermes_home() / "memories"`.
  Built-in memory is currently profile-path-based, not the requested per-agent namespace

No actual runtime config, provider credential, model, remote executor or memory-server
identity has been invented. Effective redacted config identity and runtime host versions
belong in the receipt generated in the actual test environment.

## 2. Observed execution paths

The following are **static traces**, not five executed end-to-end turns. Paths are relative
to the repository root. All routes converge on `run_agent.AIAgent` and its
`agent/turn_facade.py::TurnFacadeMixin.run_conversation`, which admits the durable turn lease
before calling `agent/conversation_loop.py::run_conversation`. The latter uses the existing
`agent/turn_context.py::TurnContext` and topical turn modules; no parallel agent loop or
second TurnContext is needed.

| Route | Construction and execution seam | Current scope and gap to test |
|---|---|---|
| CLI | `hermes_cli/main.py` chat dispatch → `cli.py::main` / `HermesCLI` → `hermes_cli/cli_agent_setup_mixin.py` constructs `AIAgent` → `hermes_cli/cli_chat_turn_mixin.py` invokes `run_conversation` | Launch/profile config and SessionDB flow exist; prove future explicit agent binding matches other routes |
| TUI / desktop shared backend | `hermes_cli/main_tui_launch.py::_launch_tui` → Node UI → `tui_gateway/entry.py::main` stdio JSON-RPC; desktop also reaches the shared gateway via WebSocket. `tui_gateway/server.py::_make_agent` constructs the agent; `methods_prompt.py` handles `prompt.submit`; `prompt_turn.py::_run_prompt_submit` executes it | `server.py::_profile_scoped` and `model_switch.py` bind profile home/secrets/terminal scope. Renderers do not own inference. Test reconnect/held approval and same-profile identity independently |
| Messaging gateway | `gateway/run.py::main` → `GatewayRunner` message/agent processing → `gateway/run_turn_runner.py::TurnRunner._build_fresh_agent` (or cached agent) → `_run_conversation_with_approval` → common facade | `gateway/run.py::_profile_runtime_scope` binds home/secrets/terminal policy. Cached agent reuse and delivery have separate lifecycle; test stale scope and delivery failure |
| Cron | `cron/scheduler.py::run_one_job` / `run_job` → `_construct_cron_agent` → `_run_agent_with_watchdog` → common facade; `cron/scheduler_provider.py::_profile_cron_scope` handles owning profile | Actual construction uses `skip_memory=False`, `skip_background_review=True`, platform `cron`. An old guide's skip-memory description is not authoritative. Future job records must bind the intended agent, not merely the profile |
| Delegated child | `tools/delegate_tool.py::_build_child_agent` → child `AIAgent`; `_run_single_child` → `_ChildRun` in `tools/delegate_tool_child.py` → child `run_conversation` | Child has platform `subagent`, lineage and fresh iteration budget (`iteration_budget=None`), `skip_memory=True` and memory tool exclusion. Requested individual child memory is new BE08 work, not an existing feature or a safe toggle |

### Common identity fixture specification (proposed)

Reuse each route's established fixture conventions; do not create a parallel agent harness.
Use synthetic IDs only, with two on-disk profile homes A and B, two same-name projects with
distinct IDs, a trusted-configured primary identity, a stable specialist and an ephemeral
child. All names/IDs in tests are test data, never deployed identities. Give A/B conflicting
non-secret canary values and fake credential names/values. Enter A → B → A under active
multiplexing, then exercise thread hops, background review, teardown and child construction.

For each route assert the same intended `(profile, agent, project, policy version,
memory backend, allowed MCP server/tool set)`; assert a missing scoped secret stays missing,
and process environment is unchanged. A same-profile specialist must not acquire primary
memory authority from a name, prompt, shared project, cache hit or reconnect. This full
cross-route agent-policy matrix is **not yet implemented or certified**.

### Inherited contracts and grounded gaps

| Observed capability | Preserve / bounded gap |
|---|---|
| `agent/turn_facade_lease.py`, `hermes_state_compression.py` | Durable holder leases and renewal exist; BE02 must extend authoritative fencing to effects/commands/children, not claim a proposed integer generation already exists |
| `hermes_state_messages.py::archive_and_compact` | Transactional watermark compaction preserves concurrent tails/originals; extend failure/restart coverage without losing opaque provider sidecars or exact anchors |
| `agent/iteration_budget.py`, child construction | Per-agent iteration limits exist; not a durable aggregate tree spend reservation |
| `tui_gateway/contracts/`, generated shared TypeScript/OpenRPC | Existing typed JSON-RPC contracts are the integration base; new runtime command/event semantics must be versioned additions |
| `tui_gateway/event_replay.py` | Bounded per-session event/byte rings with process epoch and truncation signals; not durable restart replay of mission state |
| `hermes_cli/goals.py`, `tui_gateway/methods_session_control.py` | Goals and control snapshots exist; richer missions extend them |
| `tools/mcp_tool_agent.py`, `tools/mcp_tool_health.py` | Generation-fenced tool refresh and uncertain mutation outcomes exist; prompt/resource list-change notifications are logged and ignored |
| `tools/computer_use/cua_backend.py::sanitized_cua_driver_env`, `permissions.py` | Sanitizer/import-failure fallback can return ambient environment. This is a hardening lead for T03; exploitability and impact are not established by source reading |
| `gateway/delivery_ledger.py` | Best-effort persistence and bounded at-least-once recovery, with visible possible-duplicate markers; current ledger failure does not block sending |
| `cron/delivery_queue.py` | Dead claimed send becomes unknown and is not blindly retried; retain this distinction during delivery policy migration |
| `tools/async_delegation.py` | Durable dispatch/completion/delivery claims coexist with process-local live children; recovered results do not prove arbitrary child execution resumption |
| `agent/agent_init.py::_init_memory`, memory provider/manager modules | Built-in store/provider lifecycle exists; primary-only personal MCP plus per-agent built-in memory routing remains BE01/BE05/BE08 work |

Root/area docs are useful orientation, but actual readers win on drift. Besides memory and
child concurrency (`tools/delegate_tool_config.py` currently defaults to 10), the runner has
changed: current `scripts/run_tests.sh` honors explicit `HERMES_PYTHON` with pytest when not
activated, otherwise validates/prepares the PM activation test environment. Do not rely on an
older description that it only probes `.venv`/`venv`.

## 3. Representative existing fixture groups

Every file below existed when inspected. “Existing” means available starting evidence,
not passed in this work. Most fixtures include doubles; none alone is a live-provider,
hostile-code or whole-surface end-to-end certification. Extend behavioral invariants in
these owners rather than copying a second test harness or asserting source-text shape.

| Group | Existing files | Baseline contract / remaining extension |
|---|---|---|
| G01: profiles, agents, projects | `tests/hermes_cli/test_profiles.py`; `tests/tui_gateway/test_profile_terminal_scope_entrypoints.py`; `tests/tui_gateway/test_projects_rpc.py`; `tests/tools/test_mcp_connect_secret_scope.py` | Existing profile/project and secret-owner paths; add one explicit same-profile primary/specialist/child identity across all five routes |
| G02: held approval | `tests/tools/test_approval_gateway_wait_late_choice.py`; `tests/tools/test_approval_interrupt.py`; `tests/gateway/test_approval_send_timeout_ambiguity.py` | Late choice, interrupt and uncertain prompt send; add durable approval input-digest/revision mismatch and reconnect |
| G03: provider timeout and cancellation | `tests/agent/test_provider_client_cancel.py`; `tests/agent/test_interrupt_propagation.py`; `tests/agent/test_local_stream_timeout.py` | Existing stop/timeout behavior; add aggregate reservations and honest upstream cancellation uncertainty |
| G04: ownership and effect acceptance | `tests/hermes_state/test_session_turn_lease.py`; `tests/agent/test_cross_process_turn_lease.py`; `tests/agent/test_turn_facade_lease.py` | Lease/fenced writes; generic crash-after-external-acceptance reconciliation is a BE06 gap, not established by lease tests |
| G05: lost delivery | `tests/gateway/test_delivery_ledger.py`; `tests/cron/test_delivery_queue.py` | Recovery markers versus unknown dead claims; add shared execution/delivery receipt semantics and retry without rerunning model/tools |
| G06: compaction | `tests/hermes_state/test_compression_watermark_commit.py`; `tests/agent/test_compression_commit_fence_race.py`; `tests/agent/test_pre_compress_memory_context_handoff.py` | Tail survival, lost-lease commit and handoff context; add repeated restart/torn checkpoint and exact artifact/approval anchors |
| G07: unavailable memory | `tests/agent/test_memory_provider_unavailable_warning.py`; `tests/agent/test_memory_provider_init.py`; `tests/agent/test_background_review_memory_scope.py` | Unavailable-provider warning/init and review scope; warning-only tests do not prove primary harness outage/deletion behavior or per-agent isolation |
| G08: budgets and delegation | `tests/agent/test_run_budget.py`; `tests/agent/test_iteration_budget_race.py`; `tests/tools/test_async_delegation.py`; `tests/tools/test_async_delegation_orphan_sweep.py` | Existing limits/races/result recovery; add durable tree reservations and individual child-memory lifecycle |
| G09: MCP refresh/trust | `tests/tools/test_mcp_trust_gating.py`; `tests/tools/test_refresh_agent_mcp_tools.py`; `tests/tools/test_mcp_connect_secret_scope.py` | Trust, snapshot refresh and scoped reconnect; add same-profile grants, indirect access, malicious readOnlyHint and prompt/resource refresh policy |
| G10: schedule and API | `tests/cron/test_scheduled_occurrence.py`; `tests/tui_gateway/contracts/test_generated.py`; `tests/tui_gateway/test_tui_gateway_event_replay.py`; `tests/tui_gateway/test_session_control.py` | Occurrence identity, generated schemas, bounded replay and controls; add durable restart cursor, duplicate command and snapshot/subscription race |

Existing `evals/compaction`, `evals/tool_search`, `evals/toolperf_abeval`,
`evals/codebase_navigability`, and `evals/gateway_failure_ownership` are candidate owners for
later evaluations. Their presence is not a benchmark result. Filesystem counts vary and are
not completion criteria.

## 4. Safe reproduction protocol

1. Record actual HEAD, branch and dirty paths; preserve pre-existing changes. Record the
   selected fixture paths and all dependency digests before invoking setup
2. Use a fresh disposable development `HERMES_HOME` and `HERMES_RUNTIME_DIR` before any
   import/setup. Never load production configuration, `.env`, histories, personal memory or
   connected-provider accounts for a baseline. Use synthetic fixtures and fake secrets
3. Prepare the checkout's Python 3.14 through PM per the developer workflow. Build the
   independent environment at an unused output path; do not delete an existing environment
   opportunistically. Example after the prepared interpreter is available:

   ```bash
   python -m pm.build_env --source . --out /absolute/disposable/test-env --group dev --group test
   export HERMES_PYTHON=/absolute/disposable/test-env/bin/python
   ```

4. Run only through `scripts/run_tests.sh`, which scrubs credentials and `PYTHONPATH`, sets
   UTC/locale/hash seed and isolates files in fresh subprocesses. Bound parallelism explicitly
   to make machine load and failure diagnosis reviewable. For example:

   ```bash
   scripts/run_tests.sh -j 2 \
     tests/hermes_state/test_session_turn_lease.py \
     tests/agent/test_turn_facade_lease.py \
     tests/hermes_state/test_compression_watermark_commit.py \
     tests/gateway/test_delivery_ledger.py \
     tests/cron/test_delivery_queue.py
   ```

   Run other G01–G10 files only when the prepared environment supports their imports. A
   dependency/setup failure is blocked collection, not a behavioral regression. Retain the
   first failing output and report pass-on-retry as flaky, not an unqualified pass
5. Capture exact command, UTC start/end, exit code, interpreter/runtime versions,
   OS/architecture/backend, environment/lock identity, test counts, skips, failure names,
   retries and output digest. Record redacted fixture config and a digest of that sanitized
   snapshot only; do not hash raw secrets and publish the hash as “anonymization”
6. Review the changed diff and manifest links; run affected behavioral files after code
   changes. Full required regression belongs to the release gate, not each documentation edit

The generic paths above are instructions, not executed-command receipts. Windows requires
its native interpreter path and native platform lane. Do not simulate OS behavior by patching
`sys.platform`. Scope/I/O/security changes need real imports and temporary homes, including
A → B → A; mocks alone cannot establish the isolation claim.

### BaselineManifest minimum fields

Each receipt must carry `source_sha`, dirty-file/diff identity, `dependencies_digest`,
`config_redacted_digest` (or an explicit unavailable reason), `runtime_versions`, `os_backend`,
`fixture_version`, feature flags, command, timestamps, exit status, observed results and
artifact digests. Schema/version values are recorded from the code under test, not guessed.
A baseline runner may collect this envelope; it must not start the application or read secrets
merely to inventory it. Separate setup, collection, behavioral and live-service failures.

### Execution receipts (maintained by the implementation owner)

Measured receipts are in [baseline-manifest.json](baseline-manifest.json) and the
[BE00 journal entry](../../buildjournal.md). The canonical nine-file campaign ran on
isolated Python 3.14.7 / pytest 9.1.1: **174 passed, 2 failed**, 19.3 seconds.
The two unmodified project-tree tests see an extra `tmp` node; retain these inherited
failures for BE07 diagnosis. PM build and offline lock verification passed; the separate
YAML smoke file passed 9 tests. No runtime source or dependency changed.

Reproduce a manifest without execution with:

```bash
python scripts/ryoko_baseline.py --output baseline.json
```

Add `--run`, with `HERMES_PYTHON` pointing at the prepared test interpreter, to execute
the eight selected inherited fixture files through the canonical runner. The helper's own
behavior tests live in `tests/scripts/test_ryoko_baseline.py`. The recorded campaign includes
those two tests as its ninth file. A fresh manifest starts every result at `not_run`.

Five-route common agent identity parity, live provider measurements, actual personal MCP
contract, real external effects and hostile-code executor isolation remain **not run / not
certified**; their later owning phases must provide actual execution evidence.

### Later paired live-model protocol (proposed)

Before any live trial, declare its fixture revision, baseline/candidate SHAs, model/provider
version (or documented drift), sampling settings, authorized sources/tools, config, limits,
repetition count, ordering/randomization, stop conditions and acceptance criteria. Use matched
synthetic tasks and independent held-out episodes; record every attempt, retry, failure,
unknown billed usage and partial artifact. Compare task acceptance and user cleanup time,
then billed/estimated tokens and cost, first-response/full-run p50/p95 and uncertainty. Cache
warm/cold state and missed-tool recovery must be included. Do not repeat external mutations
to measure a candidate; use recorded responses or isolated test accounts. Candidate source
percentages and latency targets are hypotheses until measured. Holdout/training data and
private payload export need their own approved scope.

## 5. Initial threat model and pilot boundary

**Proposed first certification target:** one trusted owner on one local host, one deployable
Hermes runtime, SQLite, private CLI/TUI sessions, a synthetic creator/builder workflow that
creates and precisely revises one Markdown artifact in a disposable project. The actual
OS/provider/executor combination is selected only after environment facts and gates are
recorded. The currently available host is not automatically the user's deployment choice.
No pilot is certified or enabled by this document.

Trust assumptions and protected assets:

- Host owner, checked-out reviewed runtime and deliberately installed extensions are trusted
  for this initial envelope. Compromised host/root, malicious host administrator and mutually
  hostile tenants are outside the initial certification claim
- Generated code, child requests, web/repository/document contents, MCP descriptions/results,
  model output and remote messages are untrusted. A trusted host does not make those inputs
  safe or turn prompt instructions into permission
- Protect provider/harness credentials, each agent's individual knowledge, private project
  files, session/approval identity, artifact versions, durable commands/effects and budgets
- Profile homes and prompt/tool filtering are logical scopes; same Unix user/process or a
  shared kernel alone does not establish a sandbox. Local terminal execution must not be
  advertised as safe for hostile code before enforced filesystem/process/egress evidence
- A shared channel adds recipient/author/confused-deputy boundaries. Initial private-surface
  fixtures do not certify Slack/other shared-channel delivery or privacy
- Provider, MCP, browser, auxiliary model, telemetry and training destinations each require
  purpose/recipient policy. A local main model is not proof that the whole run is local-only

Before real sensitive data or unattended effects: fail-closed identity/grants and sanitizer,
credential-free generated-code execution under a demonstrated boundary, direct/indirect
file/network denial, approval digest/expiry checks, durable intent and honest unknown-effect
receipts, bounded budgets, redaction/retention and restore/deletion evidence. If the chosen
host cannot enforce these, refuse that capability or choose a separately approved executor;
do not relax the claim. Zero violations in a finite suite is a release gate, not a proof
against all attacks. Remote execution, additional OSes, hardware/LAYA, shared channels,
multi-host storage and public exposure need separately recorded rollout decisions.

## 6. Reviewable vertical-ticket register

All tickets below are **proposed**; no issue creation, implementation completion or release
is asserted. Each row is intentionally smaller than a whole phase. Within a phase, take A
before B unless the owning plan says a safety slice must be pulled forward. The linked phase
retains full requirements, dependencies, additional domain breadth and exit criteria; these
initial tickets do not silently drop the remaining scope. Optional/conditional work remains
visible and disabled until selected.

| Phase / prerequisites | Ticket A: narrow first change and proof | Ticket B: next bounded change and proof |
|---|---|---|
| [BE00](../../BackEnd_BuildPlan.md#be00), none; U31/U33/U35, T01 | BE00-A: source/config/dependency manifest plus inherited fixture receipt; rerun yields comparable identity and honest failure categories | BE00-B: five-route trace and threat/ADR register; review all evidence as observed, measured or proposed |
| [BE01](../../BackEnd_BuildPlan.md#be01), BE00; U01/U10/U31/U28, T02/T04 | BE01-A: trusted configurable primary/specialist/child identity with immutable binding; reject forged/unknown identities in one construction route | BE01-B: typed redacted effective policy and provenance through CLI/TUI/gateway/cron/child; A→B→A plus same-profile parity, no ambient secret fallback |
| [BE02](../../BackEnd_BuildPlan.md#be02), BE01; U02/U03/U30/U32, T06 | BE02-A: accepted-command dedup and writer fencing atop existing lease/store; two workers and crash at acceptance produce one authoritative run | BE02-B: generated narrow command/snapshot/replay contract with restart cursor; stale/expired cursor returns explicit snapshot requirement and compatibility fixture |
| [BE03](../../BackEnd_BuildPlan.md#be03), BE01/BE02; U04–U06, T05 | BE03-A: durable root/child inference reservations including auxiliary calls and retries; parallel/restart test cannot oversubscribe allocation | BE03-B: bounded admission plus cancel receipt across queue/tool/child/human wait; stopped local work and remote unknown outcome remain distinct |
| [BE04](../../BackEnd_BuildPlan.md#be04), BE01–BE03; U07/U08/U27 | BE04-A: one provider capability/wire round trip and bounded retry policy; preserve opaque fields, usage and account/endpoint scope | BE04-B: immutable catalog plus session-authorized tool view; discovery/search/direct invocation agree and failover cannot expand destination grants |
| [BE05](../../BackEnd_BuildPlan.md#be05), BE01–BE04; U09–U12/U14, T03/T04/T10 | BE05-A: deny CUA launch and permission probe when sanitizer fails; seeded ambient secret never reaches either child path | BE05-B: one executor and personal-MCP grant boundary; same-profile child denied via tool/search/shell/HTTP/reconnect, plus explicit prompt/resource refresh policy |
| [BE06](../../BackEnd_BuildPlan.md#be06), BE02/BE03/BE05; U15/U16, T07/T08 | BE06-A: effect intent/unknown state and one read-only reconciliation adapter; crash after acceptance never blindly repeats mutation | BE06-B: independent durable delivery receipt in one adapter; failed/lost delivery retries without rerunning agent, preserves legacy lane distinctions |
| [BE07](../../BackEnd_BuildPlan.md#be07), BE02/BE05/BE06; U20, F01/F03/F04/F10/F11/F32 | BE07-A: extend existing project IDs with grants and one Markdown ArtifactVersion; cross-scope same-name project cannot read ungranted bytes | BE07-B: exact revision/CAS, evidence anchors and derivative invalidation; stale edits branch, complete downloaded bytes match digest |
| [BE08](../../BackEnd_BuildPlan.md#be08), BE01/BE02/BE05/BE07; U19–U24, T09 | BE08-A: built-in per-agent owner/lifecycle and Ryoko-only actual MCP adapter; same-profile agents, outage and disabled-provider zero-payload tests | BE08-B: record correction/CAS/deletion and compaction checkpoint/rehydration; repeated restart retains exact anchors and stable cache prefix; U23/U24 remain conditional |
| [BE09](../../BackEnd_BuildPlan.md#be09), BE03–BE08; U26, F02/F09/F10/F26 | BE09-A: extend goal to bounded two-artifact Mission and deterministic acceptance receipts; missing output yields partial/blocked, not complete | BE09-B: steer/approval revision and no-progress/cancel transition; affected approvals invalidate without discarding accepted artifacts |
| [BE10](../../BackEnd_BuildPlan.md#be10), BE05–BE09; F07/F08/F09/F12–F15/F25/F26/F28 | BE10-A: two authorized research source types and freshness/provenance manifest; missing or conflicting authority remains visible | BE10-B: one selected production package with complete validated export and precise revision; known-answer/render/openability fixture and cleanup-time baseline |
| [BE11](../../BackEnd_BuildPlan.md#be11), BE07–BE10; U25, F11/F18/F19 | BE11-A: immutable manual workflow inputs/steps/output with mission-linked runs; second varied input succeeds without recipe mutation | BE11-B: draft/tested/approved/revoked lifecycle and held-out evaluation; replay avoids live duplicate effects and revocation denies new execution |
| [BE12](../../BackEnd_BuildPlan.md#be12), BE02/BE03/BE06/BE09/BE11; U17, F08/F20–F24/F33 | BE12-A: owning-agent schedule version/occurrence identity plus durable bounded review job; DST/restart/overlap and immutable input fixtures | BE12-B: one meaningful monitor and accepted commitment record; noise suppressed, source outage visible, expiry/revoke prevents new effects |
| [BE13](../../BackEnd_BuildPlan.md#be13), BE03/BE05/BE08/BE09/BE12; U18, F16/F17/F27/F29–F31 | BE13-A: one durable specialist task/result/artifact handoff with narrowed live grants and individual memory; recovered completion distinct from resumed execution | BE13-B: selected scoped executor and small-team comparison; cancellation/lease loss/staged-output conflict and same-work handoff show no leakage or duplicated work |
| [BE14](../../BackEnd_BuildPlan.md#be14), BE01/BE02/BE05/BE06 and selected workload; U13/U28/U29/U34/U35 | BE14-A: redacted inspect and previewed repair for one unknown-effect/delivery case; wrong-profile repair and mandatory-journal failure deny safely | BE14-B: manifest/offline extension lifecycle, retention/deletion and backup/restore drill for chosen deployment; bounded optional-sink outage and compatible rollback |
| [BE15](../../BackEnd_BuildPlan.md#be15), BE01/BE02/BE03/BE05 and narrow BE14; U36/U37/U41, T11/T12 | BE15-A: typed decision contract with off/shadow receipt and bounded failure; fake-node shadow proves unchanged authority | BE15-B: conditional Router-first Jetson provisioning with verified model/license/runtime, authenticated encrypted LAN and metadata-only logs; measured health/latency/firewall evidence |
| [BE16](../../BackEnd_BuildPlan.md#be16), BE15 plus each point's owner; U38/U39/U41, T13/T15/T17/T18 | BE16-A: inspect intention dataset then evaluate one DP16 candidate through calibrated held-out/log-only shadow; report tool recall/no-tools precision and recovery | BE16-B: only after promotion gate, cache-safe selected bundles with always-available authorized search/reopen; paired non-inferior success and net cost/latency, per-point rollback |
| [BE17](../../BackEnd_BuildPlan.md#be17), BE14/BE15; DP05 additionally BE08 and real integration; U40/U41, T14/T16/T17 | BE17-A: approved synthetic/redacted dataset manifest, independent labels and train/calibration/holdout separation; seeded deletion/export and leakage tests | BE17-B: selected tiny reproducible model build and signed full-bundle rollback; later DP05 harness dataset reflects actual operations and no private export is implied |
| [BE18](../../BackEnd_BuildPlan.md#be18), selected slice's mandatory gates; U32/U33/U35 | BE18-A: exact release candidate with consolidated fault/security/load/migration and paired quality receipts, then FE13 journeys | BE18-B: narrow API/TypeScript fixture handoff and later Dots OD00 compatibility proof; one runtime/scheduler canary with overlap-free cutover and tested rollback |

Each ticket's review record must include objective/source IDs, prerequisite state, actual
base/head SHA, changed files, contract/schema versions, red/green behavioral proof, exact
commands/results, migration/rollback, known failures and next dependency. The existing root
[build journal](../../buildjournal.md) is the only implementation chronicle; append under the
owning phase. A docs-only ticket can complete its documentation deliverable without marking
the corresponding runtime phase complete. The [ADR register](decisions/README.md) identifies
choices that need evidence or configuration before dependent tickets can ship.
