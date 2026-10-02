# Project authority, immutable artifacts and source context

BE07 extends the existing per-profile `projects.db` catalog and the existing SessionDB artifact catalog. It does not put shared projects in personal memory, invent a second project list, or copy the BE06 result blobs to a new store.

## Owners, grants and revisions

Existing project IDs, slugs, folders and legacy behavior survive the additive project migration. Strict project access requires both the immutable agent policy's exact `project_grants` IDs and live per-principal/per-agent project grants. No wildcard, primary-agent exception, current-working-directory inference or inherited sibling grant authorizes reads. Revocation is checked on every access. Project grant guards serialize dependent authoritative metadata transactions against revocation, in project-store then state-store lock order.

An owned user transport can explicitly create a project, claim a legacy ownerless project, inspect its owned catalog, update metadata by revision CAS and set exact grants. These controls cannot be minted by a model run, even with inherited transport context. Creation/adoption does not add model grants or rewrite agent configuration. After an administrator changes immutable agent policy, use a new session. Existing strict broad discovery/tree/project mutation routes refuse instead of leaking another project's catalog; identity-absent legacy UX is unchanged.

`projects.db` owns purpose, folders, selected source/artifact references, mission associations, owner and grants. Every existing project/folder mutation advances its revision. Its selected artifact references may deliberately name an older version. SessionDB `artifact_heads` alone owns current same-artifact version CAS. Filing a selected project reference is a separate reversible transaction, never a distributed atomic commit.

## Immutable bytes and exact editing

Schema 36 preserves BE06 artifact identities and blobs while replacing the old one-result-per-command uniqueness constraint with a partial index for runtime results. Project versions use that same catalog, with reserved/committed visibility, lineage, provenance, validation, exact approval and derivative references. Unique version reservations precede publication; a stale expected head retains a reviewable immutable branch instead of overwriting current work.

The first complete file adapter is UTF-8 Markdown, bounded to 8 MiB in the service and 65,536 characters at the initial JSON-RPC upload control. Every read verifies the full immutable digest/size and returns bounded base64 chunks; authorization applies before bytes leave. `preview_mode=plain_text` is mandatory: Markdown/HTML/scripts are source text, never executable preview content. Paths/locators are not accepted from clients or exposed as read authority. Complete download and external sharing are different operations; this phase enables no external sharing.

Targeted edits name exact ATX heading sections and their expected byte digests. Untouched and locked sections remain byte-identical. Ambiguous anchors or changed bases refuse. Three-way merge applies only explicitly selected section deltas whose current content still matches the immutable baseline; conflicts remain branches. Declared derivatives become stale when their source head changes. Reverting a selected reference never erases history.

## Human artifact controls and effects

`runtime.artifact.prepare` creates an accepted `artifact` command and exact pending approval under the existing journal/turn lease. It does not publish bytes or start inference. `runtime.artifact.publish` checks the same request bytes, approval digest, live grants, original owner/generation and deadline, then publishes and commits its immutable version/head outcome. Edit and merge have corresponding prepare/publish methods. Status and cancellation are independent controls.

Artifact control has a separate runtime context, no provider client and no authority to dispatch ordinary tools/models. Its original lease is at most 300 seconds, never renewed by replay; configured runtime budgets retain their original root/deadline. Cancellation or expired/replaced ownership stops publication, not just the UI. Explicit cancellation of a still-owned control records `effects_undone=false`; unknown external/local effects are retained. A finalized control is read through status, not rerun.

Project publication uses a distinct broker/effect class and normal cancellation/approval checks. It never borrows the cancellation-exempt BE06 final-result preservation path. One-time approval consumption, intent admission and stable effect identities prevent duplicate mutation. Confirmed publication before catalog commit remains separately recoverable as `published_uncommitted`; read-only effect reconciliation and recovery do not move the head or claim completion.

## Captures, templates, evidence and resume

Captures retain immutable original artifact bytes, acquisition time, source URL metadata, annotation, extraction attempts and reversible filing. URLs are not fetched. Extraction status is an explicit supplied result, not a pretend extractor; failure leaves original bytes readable. Equal digests only produce deduplication suggestions, preserving distinct dates/annotations and never consolidating/deleting automatically.

Templates store explicitly selected structure/style/assets/slots/exclusions against an immutable baseline and version lineage. Incidental document content does not become a reusable preference or personal-memory entry.

Evidence anchors describe source spans, exact IDs, decisions, constraints, approvals and artifact versions with authority/freshness/validity metadata. Those labels do not grant execution or make a claim true. Resume assembly joins bounded authorized project references, current versions, same-actor mission status and explicit blockers. Missing/stale/unavailable references remain visible as blockers; counts/limits and truncation prevent a bounded projection from claiming completeness. No personal-memory or transcript reader is used.

## Recovery and rollback

Preserve schema36-aware readers and the runtime-result partial index, immutable versions, sources, consumed approvals and unresolved effects. Schema35 binaries cannot safely interpret multiple project versions per command; rollback disables new classes rather than downgrading readers. Do not downgrade by deleting rows or copying newer bytes over old versions. Garbage collection remains disabled pending reference/retention/backup policy. Live services, binary extraction, active-content rendering, external sharing and full frontend editing UX are not certified by these local backend checks.
