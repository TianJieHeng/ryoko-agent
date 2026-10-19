# Upgrade `/sessions`: memory-harness conversation picker

## Goal and acceptance criteria

From the running Ryoko Hermes CLI, `/sessions` opens a selectable conversation list populated from the configured memory harness. Conversation names are the primary labels. Up/Down moves the selection, Enter opens it, and Esc returns to the current conversation. Selecting a thread prepares its history and automatically relaunches Hermes in that conversation, in the same terminal and profile. The user never needs to exit manually or copy a session ID.

“All conversations” means all threads exposed to the current authorized owner by the harness, across every result page—not only ten local CLI sessions. It does not mean other profiles' or other people's private conversations. Duplicate names are supported; immutable remote identifiers select the target.

Completion requires all of the following:

- A harness thread with no local session appears by name and can be continued with its available history.
- Existing local counterparts resume without duplicating history or creating a second remote thread.
- The selected remote identity remains attached to new turns after relaunch and compression.
- Pagination makes every authorized result reachable; the UI never silently truncates to its first page.
- Cancellation, offline/authentication errors, invalid transcripts, and failed preparation leave the current conversation usable.
- Browsing does not call a language model or send a synthetic chat message.
- The current Ryoko theme, artwork, profile, and approved runtime settings survive relaunch.

This document is a build plan only. No session feature implementation or live harness API calls were performed for this review.

## Repository findings

Reviewed checkout: `3d026ae330`, with the existing local `hermes_cli/banner.py` branding modification present. Preserve that change throughout implementation.

| Area | Existing implementation | Implication |
| --- | --- | --- |
| Slash registration | `hermes_cli/commands.py` registers `/sessions` and `/resume`; `cli.py` dispatches commands | Extend the existing command, without introducing a competing command path. |
| Current `/sessions` | `hermes_cli/cli_commands_mixin.py::_handle_sessions_command` prints local recent sessions; explicit arguments delegate to `/resume` | Bare `/sessions` is the picker entry point. Keep explicit local resume behavior compatible. |
| Listing | `hermes_cli/cli_session_mixin.py::_list_recent_sessions` calls `query_session_listing` with `source="cli"`, `include_all_sources=False`, and default limit 10 | Removing a source filter alone cannot expose remote harness threads. |
| Local resume | `_handle_resume_command`, `_resolve_resume_target`, and `_sync_agent_to_session` in `cli_commands_mixin.py` restore history and session state | Local in-process switching already exists. Remote import and automatic relaunch are additional work. |
| Resume history | `SessionDB.get_resume_conversations` produces separate model/display histories; compression chains resolve through `resolve_resume_session_id` | Preserve the established history projection and compression behavior. |
| Existing interactive browser | `hermes_cli/sessions_cmd_browse.py` provides the standalone local curses browser | Reuse suitable presentation/navigation concepts, but do not expose its delete operation in the remote picker. |
| Shared UI | `hermes_cli/curses_ui.py::curses_single_select` supports arrows, cancellation, and searchable original-index selection | Follow the repo's curses picker convention. Integration inside prompt_toolkit needs explicit terminal ownership. |
| Relaunch | `hermes_cli/relaunch.py` builds argv and performs platform-specific replacement; `cli.py` runs `_pending_relaunch` after `_tui_shutdown` | Reuse this lifecycle rather than invoking `exec` inside the command worker. |
| Current relaunch exception | The pending `/update` path uses `preserve_inherited=False` | Session relaunch needs its own explicit argument policy without changing update behavior. |
| Session ownership | `hermes_cli/active_sessions.py`, startup lease acquisition, and `shared_session_attach.py` | An active target must not be stolen; a preliminary ownership check is not a reservation. |
| Harness reads | The active `ryoko` profile config includes `list_conversations`, `get_conversation`, and `find_in_conversation` on `ryoko_memory`; the primary policy declares these read-only with schema pins | The relevant capabilities are configured. Their actual response shapes and completeness still require contract verification. |
| Personal-memory adapter | `agent/memory_mcp_provider.py::MCPContextPackProvider` implements bounded `recall` context packs | Recall is not a conversation catalog or full transcript; do not repurpose it as one. |
| MCP authority | `tools/mcp_tool_policy.py::require_operation` enforces identity, grants, schema pins, connection scope, and optional live-run requirements | A user-driven picker needs an authorized read path outside a normal model turn, not a bypass of these controls. |
| Remote writes | `agent/conversation_outbox.py` and `agent/conversation_worker.py`; documented in `docs/build/owned-memory-conversations.md` | Uploads use separate REST credentials and stable source/session/turn identities. Imports must not echo historical messages back out. |
| Canonical local conversations | `runtime_conversations.display_title`, documented in `docs/build/dots-conversation-display-titles.md` | Display names are distinct from globally unique legacy session aliases. Never use a name as identity. |
| Dots APIs | `tui_gateway/methods_runtime_conversations.py` and `docs/build/dots-conversation-prerequisites.md` | These are local owner-scoped stdio APIs, not the remote harness API. Their safe export explicitly is not an import/restore format. |

The harness server implementation and verified list/get response schemas were not established in this checkout review. Do not invent request parameters, title fields, pagination cursors, or a remote resume endpoint. No secret values or private conversation bodies are needed in this plan or committed fixtures.

## Proposed flow

`/sessions` → authorize current owner → load harness catalog → select immutable thread reference → prepare local resume target → persist validated history and remote binding → request clean relaunch → normal startup `--resume` → new turns continue the selected remote thread.

Keep this split into a catalog/transport adapter, a picker, a preparation service, and lifecycle integration. Add topical siblings rather than expanding the already large command mixin. Suggested module names below are proposals, not existing APIs.

## Phase 1 — Verify the harness contract and continuation semantics

1. Inspect the configured MCP schemas through the existing certified discovery path. Confirm `list_conversations` and `get_conversation` request/response schemas against their configured digests. Record a redacted contract with synthetic examples.
2. Establish the immutable remote identity: server/owner scope, source, thread ID, and any separate session ID. Determine whether titles come from explicit user naming, generated metadata, or another field. Confirm title duplication and rename behavior.
3. Verify pagination, ordering, search, archived visibility, timestamps, and transcript paging. Identify whether get returns complete ordered turns, sealed windows, summaries, or a truncated subset. The upload documentation warns that a live tail can be accepted before becoming visible in transcript/search windows.
4. Determine whether all selectable sources are writable through the existing authorized continuation contract. Confirm how an imported thread receives new turns without creating a new thread, changing its provenance, or violating source/agent ownership.
5. Trace the complete picker-read transport requirements: current identity scope, schema checks, secret recipient restrictions, physical request budgets, and runtime dispatch fences. Existing recall explicitly requires live dispatch; simply calling its handler from an idle CLI is not sufficient.
6. If necessary, define a narrowly authorized user-initiated read operation through the existing enforcement infrastructure. Never clear identity context, loosen grants, or create a fake conversation turn to make the call pass.

**Deliverable:** verified list/read/continue contract and sanitized fixtures. Any missing full-history or continuation capability is an explicit server-side prerequisite. Do not claim remote resume works merely because search or recall returns a summary.

**Exit condition:** every catalog row can be classified as resumable, currently incomplete, or unsupported, with a concrete reason. Required harness changes, if any, are identified before implementation.

## Phase 2 — Add a bounded, owner-scoped catalog service

Suggested owner: `hermes_cli/memory_sessions.py`, delegating transport to the existing certified MCP infrastructure.

1. Normalize list results to a small record containing an opaque remote reference, display title, source, last activity, optional count, pagination state, and resume availability. Do not download every transcript while listing.
2. Prefer the harness conversation name. For missing names, show `Untitled conversation` with a date/short ID. For duplicate names, show secondary metadata while retaining separate selectable rows.
3. Page on demand with a visible “load more” or equivalent navigation action. Preserve selection by immutable reference across refreshes. If the server lacks search, label client-side filtering accurately and allow further pages to be searched.
4. Set bounded requests and response sizes, cancellation, and useful loading/empty/offline/auth/schema-error states. Scope caches and cursors to the resolved owner/profile/server contract; reject stale results after scope changes.
5. Treat remote titles as untrusted display text: remove terminal control sequences, escape markup, and truncate by terminal display width. Do not print full transcripts, credentials, or raw remote error bodies in diagnostics.
6. Preserve an explicit local-only listing path, such as `/sessions local`. Remote failures must be visible rather than silently presenting local rows as the complete harness catalog.

**Exit condition:** synthetic multi-page catalogs expose every authorized item, including duplicate and unnamed titles, without loading transcript bodies or invoking a model.

## Phase 3 — Build the in-session keyboard picker

Suggested owner: `hermes_cli/cli_sessions_picker.py`; keep `_handle_sessions_command` as a small dispatcher.

1. Render a selectable list/box with conversation name first, last activity/source second, and a current-thread marker where a verified binding exists. Show availability and loading errors without making disabled rows appear selectable.
2. Support Up/Down, Enter, Esc, scrolling, search, refresh, and page navigation. Search results must resolve to the selected immutable reference, never the current list number or title lookup.
3. Use shared curses navigation where suitable. Arrange a main-thread terminal handoff from the prompt_toolkit application; do not start curses from the command-processing daemon or leave a `run_in_terminal` coroutine unawaited. `cli_modal_mixin.py` documents relevant thread/loop hazards.
4. Keep network work off the terminal drawing loop. Restore the composer, prior draft, keyboard modes, cursor, and output on cancellation and exceptions. Ignore late completions from cancelled requests.
5. Refuse switching while the agent is running; protect against a new turn starting between picker opening and selection. Selecting the current bound conversation is a no-op.
6. Preserve explicit `/sessions <id_or_title>` and `/resume` compatibility. Keep non-TTY behavior nonblocking, with a clearly described list/fallback behavior.
7. Scope the first release to the classic Python CLI used here. Do not change messaging-gateway `/sessions` behavior or pretend curses can run inside the separate Ink/Desktop renderer. Update help to distinguish these surfaces.

**Exit condition:** a real PTY exercise selects the intended row with arrow keys, handles resize and cancellation, and never sends picker keystrokes to the model.

## Phase 4 — Prepare a durable local resume target

Suggested owner: `hermes_cli/memory_session_resume.py`, using the existing SessionDB write APIs and an additive mapping schema where needed.

1. Persist a unique binding from `(owner/profile, harness identity, remote source, remote thread ID)` to the local conversation root. Include the verified import position/revision and metadata needed for subsequent continuation. Names remain display metadata.
2. Resolve existing mappings first. A matching string ID alone is not proof that a local session belongs to a remote thread. Validate ownership/provenance, then resolve compression descendants with the established session APIs.
3. For a remote-only thread, read all required pages into bounded staging storage and validate order, roles, stable message identifiers, snapshot/completeness indicators, and resource limits before making it resumable. If the server cannot provide a complete view, display the limitation and block a misleading full resume until Phase 1's prerequisite is met.
4. Import visible history as history, never as system instructions or privileged tool/runtime records. Do not fabricate missing tool calls, provider continuation tokens, cached prompts, approval grants, filesystem paths, or canonical ownership records.
5. Commit the prepared session, messages, provenance, and binding transactionally. Repeated selection, interrupted imports, and duplicate pages must not create duplicate messages or new sessions. Use server IDs/offsets rather than text equality for deduplication.
6. Preserve separate model/display history semantics. For a genuine remote-only text transcript, state that this is text-history continuation, not restoration of hidden model/tool state. For long histories, use the normal context-size policy without silently cutting off older messages.
7. For an existing local counterpart, define safe reconciliation using verified message IDs/watermarks. Never overwrite newer local turns with a stale remote snapshot or infer equivalence from matching text. Surface divergence for explicit resolution rather than merging unrelated timelines.
8. Use canonical display-title support or explicit imported display metadata; do not force duplicate remote names into unique legacy `sessions.title` aliases.

**Exit condition:** a remote-only thread becomes one durable resumable target, repeated imports are idempotent, and failures leave the current session and prior imported state intact.

## Phase 5 — Preserve remote identity on future turns

1. Trace whether the classic CLI's actual turn path reaches `conversation_outbox.capture_turn`; do not assume runtime-backed gateway capture automatically covers this frontend.
2. Resolve the verified remote binding when creating new upload records. The current outbox uses the runtime session ID; blindly keeping that behavior after remote import may create a second remote thread.
3. Keep imported historical rows marked as already sourced remotely so they are never captured as new uploads. Assign durable unique IDs only to newly admitted local turns.
4. Preserve the existing queue's immutable destination/owner binding and receipt-based delivery. Do not rewrite pending payloads under their old turn IDs or change the separate worker credential model.
5. Specify compression, branch, and reset behavior: compression continues the same remote thread; an explicit new conversation/branch must follow an intentional new binding policy rather than accidentally appending to the original.
6. Verify that a remote thread's source/agent identity is writable by this owner. Unsupported sources remain visibly unavailable until a supported continuation operation exists; never spoof an origin merely to make append work.

**Exit condition:** after resuming a remote-only thread, exactly the new user/assistant turns arrive in that same harness conversation, with no historical replay, duplicate thread, or cross-owner write.

## Phase 6 — Relaunch safely into the selected target

1. Prepare and validate the target before scheduling exit. Confirm the current transcript has been durably flushed; the existing `_end_current_session` is best-effort and should not be treated as a success guarantee for this handoff.
2. Check target ownership through the active-session registry. Preserve the supported attach/refusal behavior; do not kill another runtime or seize its lease. The new process must perform the authoritative ownership check again because a preflight can race.
3. Represent a pending session relaunch explicitly with its target and argument-preservation policy. Preserve `/update`'s current behavior while adding the session-specific case.
4. Exit through the main prompt_toolkit lifecycle, run `_tui_shutdown`, flush/finalize the old session under its identity, and release its lease before replacement. Do not reassign the old agent's session ID just before shutdown.
5. Use `build_relaunch_argv`/`relaunch` with `--resume <validated-local-id>` and explicit current-profile targeting. Resolve precedence for inherited model/provider/mode flags so stale launch arguments do not override the selected session unintentionally. Do not carry an old `--resume`, one-shot query, or unrelated subcommand.
6. Use argument arrays, never shell interpolation. Never put conversation names, transcripts, bearer tokens, or credentials on the command line.
7. Reuse normal startup restoration for the transcript, cwd, model/provider, and session state. Remote-only imports must not inherit remote-supplied YOLO/approval settings or arbitrary working directories.
8. Handle launch failure visibly and preserve the prepared session and old session for recovery. On POSIX a failed exec can be caught, but restoring an already torn-down interactive UI is not automatic; document the recovery behavior instead of promising atomic process replacement.

**Exit condition:** Enter relaunches Hermes once, in the same terminal and profile, with the selected history; no manual exit/tag entry and no duplicate process or stale lease remain.

## Phase 7 — Verification and failure coverage

Use isolated temporary profiles and synthetic harness data. Run Python tests only through `scripts/run_tests.sh`, with a prepared test interpreter as described in `AGENTS.md`. Do not create, dispatch, or use CI/GitHub Actions.

Relevant existing regression suites include:

- `tests/hermes_cli/test_cli_resume_command.py`
- `tests/hermes_cli/test_resume_model_restore.py`
- `tests/hermes_cli/test_relaunch.py`
- `tests/hermes_cli/test_active_sessions.py`
- `tests/hermes_cli/test_cli_shutdown_memory_messages.py`
- `tests/hermes_cli/test_curses_arrow_keys.py`
- `tests/hermes_cli/test_curses_ui_search.py`
- `tests/agent/test_conversation_outbox.py`
- `tests/hermes_state/test_resolve_resume_session_id.py`

Add focused behavioral coverage alongside the new modules, without freezing catalog names or inspecting source text in tests:

| Scenario | Required result |
| --- | --- |
| More than one catalog page; duplicate names; Unicode/control characters | All rows remain reachable; selection uses identity; terminal stays intact. |
| Remote-only thread, repeated selection, interrupted import | One local binding; ordered history; no duplicate rows or partial resume. |
| Empty page with continuation cursor; missing tail; large history | Continue valid paging; accurately report incomplete/oversized cases. |
| Timeout, auth failure, schema mismatch, cancellation | Current session remains usable and no identity changes. |
| Owner/profile A → B → A | No leaked catalogs, credentials, transcript caches, cursors, or mappings. |
| Local session conflict, compression child, current-thread selection | Correct lineage resolution; no overwrite or unnecessary restart. |
| Busy agent or target acquired after preflight | No mid-turn switch and no competing session writer. |
| Relaunch with explicit profile and prior model/mode flags | Correct target/profile and documented precedence; `/update` unaffected. |
| Post-resume new turn plus worker retry/restart | Same remote thread, stable new-turn identities, no history re-upload. |
| Real PTY arrow/Enter/Esc/resize and actual child startup | Terminal modes recover and selected transcript is loaded without manual commands. |

Finish with a small authorized live integration check against the configured harness: list names, select a test conversation, reopen it, submit one intentional test turn, and verify the receipt/thread identity. This is a future implementation validation step, not an action performed during this review. Separate verified live results from offline test results.

## Phase 8 — Delivery, documentation, and rollback

1. Update command help and user documentation with arrow-key selection, remote/local scope, refresh, and error recovery. Preserve `/resume` and standalone `hermes sessions browse`.
2. If configuration fields are required, register them in the existing config defaults and use the shared comment-preserving config writer. Do not introduce new behavior environment variables or hardcode the `ryoko` home path into implementation.
3. Use additive schema migration for import bindings. Back up affected local data before rollout; retain completed import records and mappings when disabling the picker.
4. Roll back the command integration without deleting conversations, messages, upload records, or the user's theme/banner modifications. An explicit local path remains available throughout rollout.
5. Report the actual tested surfaces and unresolved harness limitations. The feature is complete only when the remote-only conversation and same-thread continuation criteria pass, not when an arrow-key local picker works.

## Build order

Phase 1 is the prerequisite. Phase 2 and Phase 3 establish catalog browsing; Phase 4 prepares resume state; Phase 5 proves remote continuation; Phase 6 connects clean relaunch. Phase 7 verifies the complete path, and Phase 8 delivers it. Keep remote-only selection unavailable until preparation and continuation are both qualified, while making the reason visible to the user.
