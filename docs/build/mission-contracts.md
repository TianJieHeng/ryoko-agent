# Bounded missions and evidence-backed completion

BE09 extends the existing GoalManager and continuation queues. It does not start another agent loop or grant tools from goal text. The current runtime command/run projection remains separate from the longer-lived Mission.

## State and authority

A root-owned Mission is revisioned in SessionDB, with requested outcome, project/scope, declared deliverables and acceptance criteria, dependencies, plan steps, budget root/deadline and bounded recovery limits. States are ready, working, waiting_for_user, waiting_for_source, ready_to_review, completed, partially_completed, paused, cancelled and failed. Execution, deterministic verification, human acceptance and delivery acknowledgment are separate facts.

The existing goal row is migration input/audit or a compatibility projection, not a second mutable authority. Legacy completion without machine evidence does not become a verified strict mission. Mutations use the existing writer, owner/generation fences and expected-revision CAS. Typed owned controls and the owned TUI `/goal` path share backend parsing; unowned strict paths fail closed rather than impersonating a human control. Identity-absent legacy goals remain compatible.

Reviewed is the conservative policy. Direct mode requires explicit low-risk/low-uncertainty declarations; neither mode bypasses tool grants, exact approvals or project authority. Consequential/high-uncertainty work requires plan/checkpoint information. Missing decisive criteria/input waits explicitly instead of inventing a test or launching an endless judge loop.

Mission resume, goal set/clear and plan changes never reset the session's original BE03 root ceiling, consumed units or deadline. New scope must already be authorized by immutable agent policy and live project grants. Model-run project publication is checked against the mission admitted for that run; an old revision or changed project cannot silently keep writing. Explicit human artifact controls remain their own separate authority.

## Verification is evidence, not prose

The deterministic verifier reads committed, digest-checked BE07 artifact versions through live grants. It supports bounded existence, required Markdown sections, exact text changes, a local JSON-schema subset and linked-output consistency. Unsupported schema keywords/references, unavailable bytes, stale dependencies and uncommitted publications do not pass. Pinned accepted versions may remain valid when the criterion explicitly does not require the current head.

Receipts bind the exact criterion digest, artifact versions/digests, dependency fingerprints, verifier and observation time. The authoritative writer revalidates dependencies before accepting proof; source changes invalidate dependent work rather than all unrelated accepted outputs. A successful transport response, model `DONE`, source annotation or legacy shell exit-code claim cannot create a passing receipt.

The first test-command adapter is `isolated_python_v1`: exact declared Python source and SHA-256 run against staged immutable artifact inputs under the existing Linux namespace/chroot/seccomp executor. It requires a live admitted runtime, explicit executor grant and finite budget. At most four tests per run, 8 KiB code and five seconds per test are supported. Trusted host wait status and bound input/output hashes are persisted; stdout cannot impersonate the supervisor. No host shell, package install, network access or arbitrary project command is certified. Missing/uncertain execution receipts remain blocked; idle verification reads evidence and never fabricates a model run to execute code.

## Existing-loop integration

Strict mission verification occurs before `finish_turn_command`, while RuntimeRun, identity, lease and budget are still authoritative. Existing post-turn CLI/TUI consumers read the saved once-only decision; they do not run another strict judge or shell gate after the owner has unwound. A model stop alone is not mission completion.

No-progress evidence persists across turns, using bounded actual guardrail observations and artifact/criterion progress. Recovery choices and continuation counts are finite. Budget exhaustion, cancellation or failure retains usable committed outputs, precise remaining criteria and the next decision. It does not invent a follow-up task or undo confirmed effects. Degraded personal memory can support already-authorized artifact work, but cannot supply missing personal facts as verified evidence.

A late steer is recorded as missed/not applied when it arrives beyond the execution boundary. It identifies already-dispatched/confirmed effects and prevents stale continuation or a claim of retroactive correction. Delivery remains the BE06 outbox's independent truth; retrying notification never reruns a mission.

## Revision and approval lifecycle

Plan/input/target changes invalidate only declared affected pending or approved-but-unconsumed approvals. Consumed history and immutable outputs remain. Invalidation is explicitly distinguished from a human denial. User acceptance is an authenticated owner control after required verification, not a deterministic check implemented by a model. Waiving/changing a criterion requires an audited contract revision; it never rewrites a failed test into a pass.

## Direct versus reviewed measurements

`python -m evals.mission_policy_comparison observations.json` compares local paired observation artifacts without executing an agent or contacting a provider. Each pair must have identical task and acceptance fingerprints. It reports agent latency, comparable provider-cost basis and total active user-review deltas separately. Missing review time is unknown, never zero; metered estimates are distinct from provider receipts.

The input schema is version one with an `observations` array. Each observation contains pair_id, task_id, policy (direct/reviewed), task_digest, acceptance_digest, provenance (observed/synthetic), deterministic_passed, agent_elapsed_ms, provider_cost_micros, cost_basis (metered_estimate/provider_receipt/unavailable), user_review_time_ms and review_observation (measured_active/reported/unavailable). Unknown measurements must be null with their unavailable label. Use sanitized local measurement IDs, not private task bodies.

Synthetic fixtures verify the comparison machinery, not real human review savings. A failed criterion or incomparable task cannot establish benefit. The report makes no automatic promotion recommendation, statistical generalization or completion-authority claim. A real matched user-review study remains an operator qualification gate.

## Rollback and limits

Disable autonomous continuation while retaining mission snapshots, immutable receipts, artifacts, consumed approvals and unknown effects. Resume only after live scope/dependency checks. Never restore budget by clearing a goal row. Existing environment-dependent legacy verification-command discovery limitations remain separately reported; they are not substituted for the certified test adapter or claimed as passing validation.
