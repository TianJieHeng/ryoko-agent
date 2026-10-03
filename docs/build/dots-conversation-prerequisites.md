# Dots conversation prerequisites (schema v1)

## Qualified boundary

The additive `runtime.conversation.*` API accepts only the process-owned `StdioTransport` at the launch profile. WebSocket clients, synthetic transports, caller-selected profiles/principals/agents, and arbitrary session sources are rejected. The trusted configuration supplies principal, profile, active agent, configuration digest, and policy digest. `runtime.conversation.capabilities` returns that redacted identity proof plus the supported method names and bounds.

This is a **single-owner stdio deployment mode**, not delegated WebSocket or multi-human authentication. Dots must verify its authenticated human-to-producer mapping before exposing the BFF. The legacy generic session APIs remain unchanged and are not owner-authorizing APIs. Do not forward them from the browser.

Every conversation is the immutable persisted session/compression-root ID. Live `session_id` values returned by bind are transport handles, not canonical conversation identifiers. Principal/profile/agent/home ownership is persisted in `runtime_conversations`. A renamed agent/configuration/policy does not silently adopt an old binding: bind validates the original immutable binding before constructing an agent. A missing legacy ownership record is not automatically promoted.

## Generated contract

Python source: `tui_gateway/contracts/runtime_conversations.py` and `runtime_v1.py`. The generated TypeScript/OpenRPC files under `apps/shared/src/` are the consumer source of truth; no hand-written compatibility types are needed.

All conversation requests require integer `schema_version: 1` and reject extra keys.

- `runtime.conversation.capabilities`: redacted configured identity (including authoritative policy `role: primary|specialist` and `memory_backend: personal_mcp|builtin`), authority mode, method names, bounds, transcript format, and explicit unsupported features
- `runtime.conversation.create`: `idempotency_key`, optional `title` (200 characters); atomically writes the canonical session, ownership record, and request-digest receipt before returning. Identical retries survive restart, returning the same original conversation receipt and `created: false`; different intent conflicts. Create does not build an agent or run a prompt
- `runtime.conversation.operation.get`: `idempotency_key`; read-only `{found, operation, conversation}` for the original create/rename/archive receipt, or null fields when absent. It never creates, binds, mutates, or queues work
- `runtime.conversation.list`: `limit` (1–100), opaque `cursor`, `archived` (default false), literal title substring `query` (max 200). Stable creation-key descending pagination; cursors are query/owner scoped
- `runtime.conversation.bind`: `conversation_id`; validates ownership and immutable binding, resolves the validated compression tip, and initializes/reuses an exact-stdio live record. Returns live `session_id`, `readiness: building|ready|failed`, and a safe failure code. An initial bind is not proof of agent readiness or provider execution. Poll the same method; after ready, use existing session-bound runtime APIs
- `runtime.conversation.rename`: `conversation_id`, `idempotency_key`, `expected_revision`, `title`; durable mutation receipt and metadata revision CAS
- `runtime.conversation.archive`: `conversation_id`, `idempotency_key`, `expected_revision`, `archived`; reversible metadata visibility, not cancellation or deletion. The explicit compression lineage gets the same archive flag. Receipt replay does not undo later changes
- `runtime.conversation.history` and `.export`: `conversation_id`, `limit` (1–100), opaque `cursor`; same bounded `safe_transcript_v1` projection
- `runtime.command.receipt`: existing live-owner `session_id`, integer `schema_version: 1`, `command_id`; read-only persisted original acceptance receipt, current command status, and current durable journal revision from one consistent DB read. All six persisted states and missing IDs are represented; it never queues, claims, or executes. `accepted_input` is null for non-submit commands; submit inputs expose `{state: accepted|committed, message_id: string|null}`. Acceptance alone never invents a transcript row. `messages` contains explicit committed `{message_id, role: user|assistant|tool, kind: input|output, committed: true}` links, without message bodies or tool metadata. Page them with `message_limit` (1–100, default 100), `message_cursor`, `messages_has_more`, and `next_message_cursor`

`conversation.revision` is the new API's metadata revision, distinct from runtime journal revision. Replay responses contain their original revision/title/archive fields; use list/bind for current metadata. Idempotency receipts are not a cache and have no in-process TTL. Deleted canonical rows are not recreated by replay.

## Bounded safe transcript/export semantics

- Committed normal user/assistant text, including typed steer rows and authentic user text extracted from summary carriers by `split_user_originated_turn`, only. No in-memory pending deltas, system instructions, tool outputs/arguments, reasoning columns, provider state, hidden/model-only rows, or compression-summary scaffold
- Stable `message_id` uses the existing durable `message_uid`; copies across rotation/compaction are deduplicated. Legacy missing UIDs fall back to a physical-row identity; legacy conversations are not automatically admitted to this owner API
- `physical_session_id` and the validated `lineage` preserve the compression relationship. Branches, resets, tool sessions, delegates, or identity-mismatched continuations are not folded into the conversation
- At most 100 chunks and 262,144 UTF-8 text bytes per page. Each source chunk is at most 16,384 bytes and ends at a UTF-8 boundary. Arbitrarily long ordinary text can be consumed completely by continuing the cursor
- `text_offset` and exclusive `next_text_offset` are **UTF-8 byte offsets in the extracted safe text before control-character sanitization**, not JavaScript string indexes. For summary carriers the source is the extracted human text, never the hidden scaffold. Require each next chunk's `text_offset` to equal the previous `next_text_offset`, and concatenate ordered chunks with the same `message_id` until `text_complete` is true. `text_sanitized` reports removal of unsafe control characters; the end offset cannot be inferred from the sanitized text length
- Structured content contributes only recognized text parts, joined by newlines. `non_text_omitted` conservatively reports structured content; image URLs/data and unknown blocks never cross this wire
- The first page pins `snapshot_max_row_id`; later appended messages and later compression copies are excluded from that traversal. A new first page sees new rows. This is an append watermark, **not an immutable snapshot against rewinds, edits, deletions, or a legacy writer replacing existing rows**
- A cursor can be used interchangeably by history/export for the same owner/conversation. It cannot authorize another owner or conversation. Unsupported/cyclic/oversized lineage returns an error instead of silently truncating it
- Export is the explicitly scoped safe-text format, **not a full runtime backup, private tool transcript, or import/restore format**. Standalone summary carriers, hidden ordinary rows, model-only rows, and assistant summary scaffold are omitted. Pure-summary candidate pages can be empty with `has_more: true`; always continue their cursor. Full legacy display parity is not qualified

## Remaining integration qualifications

- `command_message_linkage: explicit` means each non-null history `command_id` is backed by `runtime_command_messages`, written atomically in the message transaction under its accepted submission or fenced claimed runtime context. Accepted submit input can commit before claim; retry/restart adopts that exact command's UID without text matching. New assistant/tool output is linked during claimed-turn flush. Compression copies retain the same UID and link. Equal text in distinct commands stays distinct
- Legacy unlinked rows and standalone steer/control inputs retain `command_id: null`. There is no content-based backfill or inferred attribution of a control input to a submit. Receipt links report the historical commitment, even if a later authorized rewind/deletion removes the row; they do not authorize recreating that input. Receipt message pagination is a live append traversal; start a new first page to observe later committed links
- Restore/import is explicitly unsupported
- Current mission storage still supports one mission per durable runtime session. This patch does not invent multiple mission IDs or aggregate unrelated child sessions
- Real agent construction/provider calls, browser/BFF owner mapping, credentials, live effects, and deployment are not qualified by the offline tests
- Existing generic session tools can still change metadata outside the new metadata revision; use an isolated server-owned producer for Dots

## Verification

Mandatory isolated runner with `HERMES_PYTHON=/tmp/dots-backend-python/bin/python`:

1. Nine-file focused/regression run: 56 passed (`test_runtime_conversations_rpc`, `test_runtime_conversations_stdio`, `test_runtime_command_receipt_rpc`, `test_runtime_rpc`, generated contract tests, runtime store, schema read probe, resume DB ownership, live profile scope)
2. Three disjoint migration/identity/lineage modules: 20 passed
3. `npm run typecheck --workspace=@hermes/shared`: passed
4. `.venv/bin/ruff check` on eleven touched/new Python implementation/test files: passed
5. Contract generation and `git diff --check`: passed

The real stdio fixture launches `python -u -m tui_gateway.entry` twice against an isolated profile, checks gateway readiness and configured identity, creates a conversation, kills only its own subprocess, restarts, then recovers the original operation receipt and canonical conversation. It builds no agent and invokes no model/provider. Atomic receipt-write failure rolls the whole conversation create back; schema upgrade retains previous sessions/messages/runtime events/effects and leaves new ownership tables empty.

### Command/transcript linkage follow-on (source-only)

The isolated mandatory runner additionally exercises real loop execution through the existing HTTPX mock transport (no provider network): explicit user/assistant/tool UID links, equal-text independent commands, acceptance before claim, restart adoption, atomic rollback, unanswered-user continuation, compression dedup, bounded receipt pages, carrier-only human text, privacy exclusions, exact UTF-8 chunk continuity, and additive schema upgrade. The combined 18-file runner passed 147 tests, including existing runtime, submit-time/queued persistence, incremental tool/text persistence, compression, and stdio restart suites. The immutable accepted-scope follow-on passed 87 tests across seven files (runtime, budgets, decisions, specialist control, and new linkage tests); its final two-file focused rerun passed 12 tests. Ruff and in-memory TypeScript/OpenRPC contract rendering pass. Generated contract artifacts must be regenerated during integration; this change contains source contracts only.
