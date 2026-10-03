# BE14 scoped operations and privacy qualification

## Status and boundary

This is a locally executable operator slice, not a claim that all BE14 release
gates are certified. Run `python -m hermes_cli.operations_cli qualification` for
the machine-readable qualification. It deliberately returns
`sensitive_ingestion_certified: false` until the live-store encryption, key
custody, remote-copy, full-profile recovery and deployment gates are established.
No live provider, real user deletion, training, key provisioning, dependency
installation, operating-system security setting or deployment was performed.

The local operator is already trusted to operate the owning profile. These APIs
are not model tools and do not introduce a remote administrator identity. A
future Dots/FE11 consumer must independently authenticate the operator, display
the full preview, and convey the explicit approval of that exact digest. Passing
an arbitrary caller-supplied actor is not supported. The context, effective
policy, persisted session identity and actual `state.db` path must all agree.
Failure never falls back to another profile, an ambient database, or a legacy
identity.

## Read-only consumers

Use the existing profile selection/environment binding, then:

```sh
python -m hermes_cli.operations_cli inspect --session SESSION
python -m hermes_cli.operations_cli audit --session SESSION --limit 100
python -m hermes_cli.operations_cli retention --session SESSION
python -m hermes_cli.operations_cli check-checkpoint --session SESSION
python -m hermes_cli.operations_cli qualification
```

Inspect and preview commands open `SessionDB(read_only=True)`. They do not create
operator state, acquire a lease, import an extension, install dependencies or
probe a remote service. Inspection exposes at most 100 effects/deliveries/budgets,
with explicit truncation. Ownership generation, waiting reason, runtime revision,
replay cursor, checkpoint metadata and selected memory backend are inspectable.
SQLite readability is measured. Remote connections are `not_probed` and LAYA mode
is `not_certified`; neither is guessed from configuration.

Audit is a separate allowlisted projection of the mandatory recovery journal.
Payloads, arguments, credential-bearing endpoints, filesystem paths, titles,
provider replies and receipt bodies never enter it. Correlation identifiers are
available to the owning operator. Optional analytics requires explicit consent
and a caller-held key of at least 32 bytes; HMAC correlation includes purpose
scope and field name. These are keyed pseudonyms, **not anonymization**.

`OptionalAuditSink` never runs on the dispatch thread. It owns one daemon worker
and a bounded queue (default 32, maximum 256), drops newest at capacity, counts
drops/failures, and never retries or launches replacement workers. A hanging
sink can retain at most one in-flight projection and the bounded queue. Closing
does not wait for the sink, and drops queued projections. The runtime does not
automatically enable or send optional telemetry.

## Preview and bounded repair

```sh
python -m hermes_cli.operations_cli preview-repair --session SESSION \
  --action reconcile-effect --target EFFECT_ID > repair.json
# Read the complete preview and approve its exact plan_digest separately
python -m hermes_cli.operations_cli apply-repair --session SESSION \
  --plan repair.json --approve APPROVED_PLAN_DIGEST
```

`RepairPlan` includes actor, session, affected IDs, target state, before revision,
invariant checks, expected outcome and a five-minute expiry. The apply consumer
recomputes the plan, refuses scope/state/revision drift, then uses the existing
session-turn lease. There is no second lease or generic row-edit endpoint.

Supported actions:

| Action | Existing authority and actual outcome |
| --- | --- |
| `reconcile-effect` | Existing read-only effect reconciler, fresh lease generation, 30-second deadline; local immutable bytes can confirm an existing effect, missing bytes remain unresolved; never repeats the mutation |
| `retry-delivery` | Existing runtime outbox repair, same recipient/result/attempt counter/deadline; additional transaction-level owner and state/attempt CAS; requeues only, does not send |
| `revoke-lease` | Exact observed generation and holder fingerprint; before/after audit and lease removal are one SQLite transaction; does not assert remote cancellation |
| `rebuild-index` | Existing cross-process FTS rebuild admission; only a single-actor profile with at most 100 sessions, 10,000 messages and 64 MiB state file; foreign/unbound actors refused; zero rebuilt is `no_progress` |

`restore-checkpoint` and `mark-success` are intentionally unsupported. There is
no certified history-rewind operation that preserves effect/approval generations
and every derived store. The read-only `check-checkpoint` consumer verifies actual
schema, config/policy/runtime/projection versions, watermark, replay retention and
context-projection linkage. It returns `restore_allowed: false` even if qualified
for an isolated drill. Preserve the original and perform that drill below. Do not edit the database to turn
an uncertain outcome into success.

`operations.repair_started` must commit before invoking an adapter. An outage at
that required write blocks dispatch. A missing completion receipt leaves the
started event inspectable and requires another preview; it does not authorize
replay. Revocation journals and its mutation are atomic. Optional exporter
failures cannot authorize, suppress, or replace the required journal.

The journal adds four closed event kinds: `operations.repair_started`,
`operations.repair_finished`, `operations.deletion_requested`, and
`operations.deletion_finished`. Existing wire projections omit their private
payloads but retain event kind, cursor and operation correlation. The runtime
wire Literal and generated clients include those kinds; the real repair/deletion
replay fixture verifies their typed serialization and private-payload removal.

## Retention and deletion manifests

```sh
python -m hermes_cli.operations_cli preview-deletion --session SESSION > delete.json
# An individual specialist memory record can instead be selected explicitly
python -m hermes_cli.operations_cli preview-deletion --session SESSION \
  --memory-record RECORD_ID > memory-delete.json
python -m hermes_cli.operations_cli apply-deletion --session SESSION \
  --plan delete.json --approve APPROVED_MANIFEST_DIGEST
```

Preview never deletes or regenerates a memory projection. It binds exact scope,
actor, source revision/fingerprint, stores, limitations and expiry. Transcript
erasure is bounded to 1,000 rows/8 MiB. Accepted/claimed commands, unresolved
effects or invocations, live competing leases and nonterminal missions block
erasure. Transcript drift without a runtime revision change is still detected.

Acknowledgment means **logical deletion**, only for these actual stores:

- Transcript: all selected session message rows, including inactive messages
  and API/provider sidecars; message FTS projections through existing delete
  triggers; session title/system-prompt reference; unreferenced deduplicated
  system prompts. Erasure and both audit events commit together or roll back
- Individual built-in memory: every logical SQLite version of the exact record,
  its conflict proposals, and both generated Markdown projections. The owning
  namespace, file-descriptor checks, existing flock, SQLite transaction and
  projection synchronization remain authoritative. Tombstones retain IDs,
  versions and non-payload structural metadata. Content, source-ref and author
  payloads are removed from all retained versions. Shared project memories are
  refused by this operator slice

Individual memory and SessionDB are separate existing stores. The request audit
commits before memory mutation. A crash between catalog commit, Markdown sync
and final audit leaves a request without closure; no complete acknowledgment is
returned. Inspect the record and projection before another attempt. Existing
store recovery regenerates its projection from its catalog. Frozen conversation
prefixes are not rewritten; already loaded copies require session termination.

Explicitly retained or unverified copies include runtime command/event/context
recovery records, mission evidence, artifacts, other session metadata/activity
fields, standalone session JSON/JSONL and request-dump files, exports, external
caches, backups, provider copies and remote personal-harness stores. BE12
schedule/monitor/commitment records and BE13 delegation roots/handoffs/private
workspaces, bounded-service source/receipt/output blobs and channel bindings are
also retained; inventory reports scoped row counts without disclosing payloads.
Unresolved handoffs and incomplete service pipelines block erasure. Their
deletion is not implied by the message or individual-record acknowledgment.
SQLite free pages/WAL, filesystem media and backup staging are not certified
forensic erasure. Existing checkpoint-bounded event pruning and protected effect
records are unchanged. No unbounded new journal, audit store or retention daemon
is created. Manifests report `complete_deletion: false`.

## Encryption, custody and recovery fixture

The chosen implemented encryption scope is an **AES-256-GCM backup envelope**,
using the installed `cryptography` implementation. It authenticates ciphertext
and header, including key ID, exact store schema, code version, archive digest
and size. A fresh 96-bit nonce is used per envelope. Unknown format, wrong key,
header tampering and incompatible versions fail before extraction. Archive
validation bounds bytes/expansion/member count and refuses duplicate members,
traversal, absolute paths, symlink members and nested archive encryption.

Keys are caller-supplied bytes in memory. The code never creates persistent
credentials, reads a key from a profile, stores a key in a backup, installs a
custody service or claims zeroization of Python memory. Production approval must
select an external key custodian, key IDs, backup-access roles, escrow/restore
procedure and retirement policy. Loss of the only usable key makes the encrypted
backup unavailable. Rotation creates a new authenticated envelope under a
distinct external key; old key retirement and old-copy expiry are separate steps.
At-rest encryption cannot protect an already compromised process holding keys.

`snapshot_session_store` reuses the existing updater backup module's WAL-safe
SQLite copier. Its explicitly supplied staging directory contains transient
plaintext. `verify_session_store_restore` restores into a new temporary directory,
checks SQLite integrity, foreign keys and exact session IDs, and removes that
directory afterward. The synthetic fixture proves key-loss, wrong-version,
tampering, rotation and data restoration without changing a live profile.

This is a **SessionDB-only drill component**, not a new tier of updater snapshots,
live DB encryption, a full-profile backup, schema rollback, or an upgrade/downgrade
certification. Existing transactional updater and frozen compatibility modules
remain unchanged. Full recovery still needs all profile stores, compatible code
and readers, old extension manifests, approved key availability, drained writers,
and a separately witnessed restore/deployment drill.

## Offline extension qualification

`python -m hermes_cli.operations_cli extension-catalog --root UNPACKED_EXTENSION`
uses the PM declaration reader, never the plugin runtime loader. Inventory is
bounded to 512 regular, single-link files/32 MiB and refuses symlinks/special files.
The Python API pins source URL, exact 40-character revision, claimed publisher,
file hashes, actual lock artifact, environment-manifest artifact and declared
capabilities from the existing enforced capability registry. Reproduction is
deterministic. Verification fails on byte/grant changes, and revocation creates
a new linked manifest which verification refuses. Disabled/revoked catalog
operations execute no plugin code and start no network or install process.

### Opt-in enforced lifecycle

`plugins.pinned_manifests` defaults to an empty registry, preserving existing
behavior. The lifecycle operator publishes only after a separately approved
preview ID:

```sh
python -m hermes_cli.operations_cli preview-extension --action pin --key PLUGIN \
  --root UNPACKED_EXTENSION --manifest PINNED_MANIFEST_JSON > pin-plan.json
python -m hermes_cli.operations_cli apply-extension --plan pin-plan.json \
  --approve APPROVED_PREVIEW_ID
python -m hermes_cli.operations_cli preview-extension --action revoke --key PLUGIN > revoke-plan.json
```

Preview binds the current profile/config, exact package/capabilities/environment
and expiry. Applying a pin reads each source file through no-follow directory
descriptors, checks its approved hash, and publishes an owner-private read-only
copy under `extension-artifacts/<source_digest>` before referencing it from
trusted profile config. The registry is outside the untrusted extension tree.
It never enables an unselected plugin, grants capabilities or installs packages.
Existing PM provisioning and selection remain authoritative.

The actual PluginManager native/portable activation seam re-reads the owning
profile's pin, revocation and existing exact grants, verifies source and sealed
artifact bytes, and checks the exact interpreter version/OS/process architecture
before import/register. Execution resolves from the sealed artifact, so a source
swap after validation cannot substitute executed code. Read-only package paths
also prevent ordinary runtime bytecode writes into the pinned tree. This is not
a sandbox against a compromised process/account that can rewrite its own config.

Pinned category/entrypoint activation is unsupported and fails closed at the
shared category, memory and model-provider loaders before plugin code runs.
Absent pins preserve existing behavior. Disabled plugins remain metadata-only.
Revocation atomically writes the registry denial and uses the existing targeted
manager unload, leaving unrelated registrations intact. Other already-running
processes require restart/drain; revocation does not claim to kill active code.

Publisher authenticity, full environment reproduction and production deployment
remain uncertified. Exact manifest reproduction is metadata/byte evidence, not a
publisher signature. The general-loader enforcement is locally fixture-proven;
future category adapters need their own enforcing contract before support expands.

## Deployment and qualification handoff

Verified here: isolated Linux SQLite/dirfd/flock behavior and synthetic local
recovery. Not verified: macOS/Windows filesystem guarantees, daemon/client
deployment, remote provider cancellation/deletion, managed volume encryption,
production key custody, contention at production scale, full backup expiry and
upgrade/downgrade disaster recovery. SQLite remains the authority; no distributed
storage, fleet, queue or training subsystem is introduced.

Dots integration is producer-side only. FE11 should render plans, explicit
approval, partial acknowledgments and unresolved repair states rather than
promoting this qualification into a green deployment/sensitive-ingestion badge.

Focused receipts and pending acceptance work are recorded in the BE14 build
journal checkpoint. The repository's standard test wrapper is authoritative;
fixtures use temporary synthetic profiles only.
