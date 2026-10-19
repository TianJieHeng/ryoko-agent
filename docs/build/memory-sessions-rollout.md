# Memory session picker: rollout and recovery

Implementation: classic Python CLI `/sessions`. The authoritative protocol findings and limitations are in [memory-sessions-contract.md](memory-sessions-contract.md); the original phases are in [upgrade_sessions_plan.md](../../upgrade_sessions_plan.md).

## Data and authorization

No new configuration keys, credentials, model tools, dependencies, service deployments or CI jobs are required. The existing profile must have its primary personal-memory owner, exact read-only `list_conversations`/`get_conversation` grants and schema pins, an explicitly unmetered bounded MCP transport contract, and enabled conversation capture. The existing standalone conversation worker continues to own the REST bearer.

The browser opens a short-lived operator read scope, outside model execution: at most 12 physical POSTs and 90 seconds, further limited by the configured runtime policy and per-request byte/time caps. Cancellation, owner/policy changes, schema changes and uncertain requests close that scope. A private profile-local `session-browser.sqlite3` records operation IDs, owner identifiers, request counts and states only. It contains no transcripts, titles, credentials or endpoints. Connections are not registered in model tool pools.

The first prepared conversation adds `memory_session_bindings` to the profile's `state.db`. Each row binds owner/profile/agent, exact MCP server/endpoint, remote source/ID/hub, local compression root, immutable push-destination digest, display name and import digest/time/reported turn count. Imports atomically enroll the local session, archive, frozen agent configuration and mapping. No remote approvals, cwd, tool results or canonical conversation ownership rows are adopted. Existing session/message/outbox rows are retained.

A local ID match alone never proves a remote counterpart: an existing mapping or matching confirmed upload receipt/hub is required. Local names are display fallbacks only. The remote reported count is a conservative divergence signal, not a proof of complete transcript equivalence. No automatic reconciliation occurs.

## Verified delivery

Local validation uses the isolated PM-built `/tmp/ryoko-sessions-testenv` and `scripts/run_tests.sh`; no CI. See the S07 build journal for commands/results. PTY coverage exercises prompt_toolkit suspension, raw arrow sequences, resize, Enter, Escape and composer restoration. Protocol tests use a real local MCP SDK server and temporary owned databases.

The authorized live check created the visibly named **Sessions picker verification** thread, selected it from the real installed CLI, accepted the archive limitation, and observed normal process replacement into its local mapping. The next actual model response correctly answered `SEAFOAM` from the archive. Six synthetic seed turns plus two newly generated turns produced eight acknowledged upload rows, eight attempts, one remote hub, and no uploads under the surrogate local ID. Re-selecting the current thread was a no-op, with one binding and three local rows (archive, user, assistant).

The same live check demonstrated the server limitation: its catalog reported all eight accepted turns while `get_conversation` still returned only the previously sealed window. Delivery receipts establish the new turns' destination; transcript-window visibility can lag. Never label this a full-fidelity remote restore.

At initial verification the harness advertised 53 existing threads, all with null titles. Two names were recovered from verified local metadata. The named test thread raised the catalog to 54; the remaining 51 records were honestly labeled untitled. Existing server titles are never renamed by this feature.

## Backup and rollback

Before live import, consistent SQLite backups of `state.db` and `conversation-outbox/outbox.sqlite3` were created in the private active-profile directory `backups/sessions-upgrade-20261018-205913/`. These contain private data and are not committed. Avoid replacing live databases with old copies: that would lose later conversations or receipts.

For an immediate local-list fallback, use `/sessions local`. To disable the new picker in code, revert only the `/sessions` dispatcher integration and its session-relaunch hook. Retain the binding resolver and outbox routing for sessions already imported, along with all binding/message/outbox rows. Removing the routing while continuing an imported session could create a second remote thread. No database cleanup is necessary to stop using the picker.

A full feature rollback should stop use of imported sessions first and preserve their records for later restoration; do not delete worker receipts or queued turns. The Ryoko theme, artwork, yin-yang label and existing Botwin.Tokyo banner edit are unrelated and must be preserved.
