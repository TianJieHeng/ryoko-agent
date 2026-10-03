# BE14 bounded local owning-store recovery

## Certified boundary

This is a Linux, local-operator drill and derived-state repair implementation. It does not perform a live profile import, downgrade, external reconciliation/redispatch, or production disaster-recovery cutover. The existing updater remains the full-profile snapshot/rollback owner; its snapshot inventory and frozen compatibility surface are unchanged.

The `ryoko-local-owning-stores-v1` bundle explicitly inventories:

- `state.db`: every owning table, including commands, journal/checkpoints/context, effects/evidence/one-use approvals, budgets, delivery/outbox, artifacts, workflows, schedules and other canonical SessionDB records
- `projects.db`, if present, including grants and canonical project references
- Every immutable `runtime-artifacts` blob in the actor's namespace, including orphan bytes retained without promoting them to committed output
- The actor's individual-memory SQLite catalog, including historical/conflicting versions and both checked Markdown projections, if initialized

Absent supported owners appear as empty inventory entries. The manifest records file size/SHA-256, exact code/store/config/policy/home/actor compatibility, per-store schema fingerprints, safety-state digests, checkpoints and explicit exclusions. Bounds are 64 MiB expanded total, 4,095 payload files, 100 sessions and 10,000 effect/approval records. Checkpoint reconstruction accepts at most 2,000 tail events/8 MiB per session. SQLite snapshots include committed WAL content via the updater's established safe copier. Writer admission uses existing backup/memory locks plus bounded SQLite locks; live session ownership refuses a bundle.

This version requires a single-actor source profile and current compatible store schemas. Unsupported populated owning stores (legacy cron/kanban/memory/gateway/pairing/plugin stores and unknown root SQLite stores) refuse certification. Empty startup scaffolds are harmless. Foreign agent data, unsafe links, missing artifact bytes, incomplete/drifted memory projections, corrupt databases, incompatible reader versions and gaps in required checkpoint replay fail closed. Existing raw config/credentials, logs/caches/exports/older backups and external project files are excluded; their root entry names and the exclusions are reported in the bundle. External personal-memory/harness providers are never copied, contacted or certified as restored.

## Isolated operator drill

Run the existing operator module in the intended source profile:

```sh
python -m hermes_cli.operations_cli drill-recovery --session SESSION \
  --code-version EXACT_BUILD_ID --temporary-parent PRIVATE_SCRATCH_DIRECTORY
```

The command creates an in-memory bundle, materializes it into a fresh private temporary directory, verifies complete inventory/digests, SQLite integrity and foreign keys, memory namespace/projection consistency, artifact bytes and project ownership/references, and invokes native SessionDB/checkpoint readers. It prints a payload-free receipt and removes the temporary copies on normal/exception exits. It does not expose a user-selected restore destination or start any copied runtime. Identity stays source-bound; no configuration or credentials are installed and no source/home identity is rewritten.

A host/process crash can leave plaintext temporary files. Scratch custody and cleanup remain the operator's responsibility. The drill does not promise forensic deletion.

For explicitly authorized encrypted archive custody, host code may pass the bundle bytes through the existing `operations_backup.seal_backup`, `open_backup`, and `rotate_backup` functions with externally supplied 256-bit keys and exact key/schema/code identifiers. Lost/wrong keys and tampered ciphertext fail authentication. No key is generated, saved or recovered by this workflow. AES-GCM protects the archive only: staging, the live database, files, logs, updater backups and the running process are not thereby encrypted or production-qualified.

## Fenced checkpoint projection repair

```sh
python -m hermes_cli.operations_cli check-checkpoint --session SESSION
python -m hermes_cli.operations_cli preview-repair --session SESSION \
  --action restore-checkpoint --target SESSION
```

Review the printed plan and save it locally. After explicit authorization of its exact digest:

```sh
python -m hermes_cli.operations_cli apply-repair --session SESSION \
  --plan REVIEWED_PLAN_JSON --approve EXACT_PLAN_DIGEST
```

This operation reconstructs only the derived runtime projection from the latest compatible checkpoint plus its complete retained journal tail. It reuses the live append path's canonical pure reducer and overlays current owning-table references. It does not rewind the journal, transcript, context bytes, command statuses, effect outcomes, approval consumption, delivery receipts or owner-generation counters. Unknown effects and missing invocation outputs remain unresolved. A fresh existing session lease fences all old owners; plan CAS prevents stale repair. Projection replacement and both mandatory repair journal events share one SQLite transaction, including when the current cached JSON is corrupt. Failed journal persistence or process death rolls back the whole repair. No model/tool/provider/delivery adapters execute.

`restore_allowed` qualifies this derived-only action when checks pass and there is no active owner. It does not authorize a live profile restore or certify that external work stopped. Full-source/store encryption deployment, production key custody/loss recovery, multi-actor/legacy store breadth and live recovery cutover still need separately approved qualification.
