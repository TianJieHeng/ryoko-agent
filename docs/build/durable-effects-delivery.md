# Durable effects, exact approvals and result delivery

BE06 extends the same SessionDB writer, accepted-command journal and existing delivery obligations. Schema 35 is additive. There is no second execution scheduler or completion authority.

## Effect truth

Intent is durable before dispatch. Stable operation and intent identities distinguish replay from a new intentional identical action. States are `prepared`, `dispatched`, `confirmed`, `failed`, `outcome_unknown`, and `reconciliation_required`. Receipts and bounded evidence retain actor/run/generation/policy bindings. A successor must acknowledge dispatched uncertainty before recording reconciliation evidence. Unresolved work is never automatically dispatched again. Cancellation does not reverse a confirmed effect.

The first certified mutation is immutable result publication on local Linux storage, not an external message or project-file promotion. `renameat2(RENAME_NOREPLACE)` publishes one bounded blob under a private actor namespace. Checked directory/file descriptors reject symlinks, hardlinks, changed owners/modes and digest/size mismatch. The cap is 8 MiB; oversized results fail explicitly rather than claim recoverable truncation. The selected reconciler reads one exact file under a bounded deadline. Matching bytes support a current-state confirmation; missing or conflicting bytes remain unresolved. It never reruns the writer. Strict consequential MCP calls remain refused pending a semantic durable adapter; pinned operator-authorized MCP reads retain their separate contract.

## Result and delivery truth

ArtifactVersion visibility, command completion and delivery intent commit in one SQLite transaction after confirmed publication. A crash before that transaction can leave published bytes without completion. Owned `runtime.result.get` reads those bytes as `published_uncommitted`; this does not complete the command or create an outbox row. Committed results report `committed` and their delivery ID. Result payloads retain final/partial text and bounded public completion metadata, not a second transcript, reasoning or provider-state store.

`runtime.result.get` reads at most 65,536 raw bytes per request, base64 encoded, with the full immutable object's SHA-256, size, offset and EOF. The client can reconstruct the full result without running inference or delivery. Artifact IDs are opaque; no client path is accepted.

The first delivery adapter is the already-owned local TUI/Desktop JSON-RPC transport. `runtime.result.available` carries an immutable reference and attempt token. A successful transport write means `transport_accepted`, never human receipt. `runtime.delivery.ack` requires the exact attempt token and digest; text and artifact receipt can be partial. `client_received` is an explicit client's claim, not proof that a person read anything. Read/status calls never create attempts. `runtime.delivery.retry` repairs and retries only this notification using the same artifact and recipient; it does not call an agent, tool or mutation.

Notification retries have three physical attempts, persisted jittered backoff and a 24-hour deadline. The explicit client-ACK acceptance window is 30 days; unresolved rows and immutable bytes are not automatically deleted. File garbage collection awaits BE07 reference/backup policy. Unknown or exhausted deliveries stay visible rather than becoming success. External gateway/cron transports remain uncertified for strict runtime execution.

## Exact approvals and recovery controls

SQLite owns pending requests, decisions and one-use consumption. Approval scope binds actor, owner generation, run, action/input/target digests, policy version/digest, input and artifact revisions, and expiry. Missing, withdrawn or timed-out UI responses stay pending. Stale or changed bindings, broad approval choices, repeated resolutions and second consumption fail closed. A process-local preview or UI callback cannot itself authorize dispatch.

Owned runtime approval/effect RPCs expose redacted records. Resolution derives the live owner from the authenticated session, never request parameters. Read-only reconciliation takes a short server-owned lease and refuses an active owner. It can record evidence but cannot dispatch work. Generic `runtime.command` approval remains unsupported; use the exact durable request API. The full frontend presentation and client acknowledgment UX belong to FrontEnd_BuildPlan consumer phases.

## Legacy mapping and rollback

Existing `delivery_obligations` rows remain `authority=legacy`; new runtime records use `runtime.v1`. Each existing row remains its sole completion authority. Legacy recovery, profile maintenance and pruning cannot rewrite runtime rows. A crash-left reply or ambiguous prior send becomes `outcome_unknown`, with original payload/recipient retained and no automatic resend. Only explicit evidence of never-dispatched/reconnect-safe work retains legacy retry eligibility. Legacy rows lack a reliable per-turn token: retained undelivered/unknown output conservatively holds automatic resume for that conversation until operator reconciliation/settlement. A later turn marker cannot silently override that hold; explicit inbound messages remain available. Holds refresh at startup/reconnect, and a flag-clear failure keeps automatic resume blocked. Lifecycle JSON remains forensic evidence, not a competing completion ledger.

Disable admission/new effect classes before rollback. Keep schema-compatible readers, unresolved records, approval consumption history, immutable bytes and outbox obligations. Do not erase idempotency history or downgrade uncertainty to retryability. This phase does not certify remote exactly-once delivery, remote rollback, vendor receipts, user-read confirmation or live services.
