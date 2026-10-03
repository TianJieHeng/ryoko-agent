# Bounded execution and specialist controls

## Scope and truth boundaries

These are FE09/FE10 slices on the existing Hermes JSON-RPC backend, not full phase acceptance. No new runtime authority, service registration, credentials, device bridge, backend, generated contract, dependency, memory backend or automatic mutation retry was added.

FE09 provides `runExecutionCommand`, `prepareRuntimeExecution` and `ExecutionPanel`:

- Inspect the two declared local document services and actual service health, authentication, input/output limits, processing and storage location.
- Prepare an exact project artifact version, disclose its digest/size and complete local route, then require an explicit review action before exact-manifest execution.
- Keep the pipeline's request identity stable across explicit re-preparation of the same source so backend recovery can reuse committed pure stages. Changing the selected source invalidates its preparation. Disconnect invalidates review authority while retaining the pipeline recovery reference within the same request/session identity.
- Inspect pending/partial/completed stage receipts, transfer digests/sizes, executor generation, disconnected/unavailable reasons, and bounded immutable output bytes. Output size and SHA-256 are verified; private contents are not dumped into the status transcript.
- Read actual mission verification and effect receipts, distinguishing focused checks, prepared/dispatched/unknown effects and separately confirmed operations. No full-suite, browser purchase/send, commit/push/merge or deployment claim is synthesized.

FE10 provides `runSpecialistCommand`, `bindRuntimeChannel` and `SpecialistPanel`:

- Inspect the owned conversation roster and failed delegation rows. Lineage visibility never implies current control authority. Steer and interrupt use the exact active session; queued steering is explicitly not delivered steering, and an accepted interrupt signal is not a terminal state.
- Preserve isolated built-in specialist memory. No primary personal memory, child transcript tail, raw error payload or private context is read or shared into a team.
- Inspect actual media declarations without recording. Speech stop, audio discard, call hangup and accepted-work cancellation remain distinct. The bounded gateway has no call hangup action.
- Bind one supported local channel to the existing mission and submit one logical, revision-bound input. The desktop form retains the attempted input identity across disconnect and requires inspection rather than retrying automatically. Backend logical-input dedup remains authoritative.
- Expert controls expose explicit selected-window PNG metadata capture, fresh-frame inspect, bounded-region annotation and exactly confirmed selected-text/transcript submission. Caller session/schema/identity/history fields and mismatched operation payloads are rejected before RPC. No automatic screen capture, OS act, OCR, recording or remote speech service is implied.

Both panels guard completion against session/request/connection changes and unmount, disable pending controls, and never cancel accepted work merely because the panel closes. All response rendering is plain React text.

## Known backend/product limits

- Named `SpecialistManifest` selection/inspection, team dependencies/budgets/synthesized results, and an executor chooser do not have a current gateway RPC. The panels say so instead of substituting unrelated process/roster data.
- Service execution is the finite local UTF-8-normalization → document-structure pipeline. It is not the planned transcription → document-processing live demonstration and does not silently move execution to a cloud service.
- Screen freshness means backend receipt age (15 seconds), not verified client acquisition time. The backend does not return a capture timestamp. UI layout changes require a fresh explicit snapshot.
- Speech adapters are normally unconfigured. These panels do not implement microphone streaming or a call UI; existing media surfaces own those tasks.
- Channel binding covers `local_jsonrpc`, `voice`, and `screen`. It does not establish an external messaging identity or prove a real cross-device handoff.
- The specialist/task form deliberately allows one logical input attempt per mounted identity. It does not offer blind resend after an unknown result.
- FE13 adds feature-local nine-locale labels/actions/disclosures using the existing locale authority; exact protocol identifiers and source content remain unchanged.
- No browser screenshot, native Electron smoke test, live device/media run, full repository suite, deployment or full FE09/FE10 acceptance is claimed by these focused checks.

## Ownership whitelists

FE09:

- `apps/shared/src/runtime-execution.ts`
- `apps/shared/src/runtime-execution.test.ts`
- `apps/desktop/src/app/runtime/execution-panel.tsx`
- `apps/desktop/src/app/runtime/execution-panel.test.tsx`

FE10:

- `apps/shared/src/runtime-specialists.ts`
- `apps/shared/src/runtime-specialists.test.ts`
- `apps/desktop/src/app/runtime/specialist-panel.tsx`
- `apps/desktop/src/app/runtime/specialist-panel.test.tsx`

Shared receipt/limitations note: this document. Both phases are registered in the existing runtime menu and TUI command surface; phase receipts separately record clean integration checks.

## Verification commands

Frontend behavior:

```sh
npx vitest run apps/shared/src/runtime-execution.test.ts apps/shared/src/runtime-specialists.test.ts
npx tsc -p apps/shared --noEmit
cd apps/shared
npx eslint src/runtime-execution.ts src/runtime-execution.test.ts src/runtime-specialists.ts src/runtime-specialists.test.ts
cd ../desktop
npx vitest run --project ui src/app/runtime/execution-panel.test.tsx src/app/runtime/specialist-panel.test.tsx
npx eslint src/app/runtime/execution-panel.tsx src/app/runtime/execution-panel.test.tsx src/app/runtime/specialist-panel.tsx src/app/runtime/specialist-panel.test.tsx
```

Actual backend contract paths (unchanged backend, isolated fixture identities/stores, no live provider/device):

```sh
HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh -q \
  tests/tui_gateway/test_media_rpc.py \
  tests/agent/test_media_ingress.py \
  tests/tui_gateway/test_subagent_snapshot.py \
  tests/agent/test_durable_delegation.py
```

The backend run passed 28 tests across four files. It exercises exact two-stage transfer recovery without replaying a committed stage, executor disconnect/reprepare, project grant revocation, foreign transport denial, owned fresh frames, unchanged logical-input dedup, actual bounded admission, persistent named specialist memory and ephemeral child isolation. This is stronger backend-contract evidence than frontend mocks, but is separate from an integrated renderer/live-backend demonstration.
