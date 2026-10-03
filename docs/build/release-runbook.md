# BE18 validation/repair checkpoint and later Dots handoff

Current checkpoint: see [final source validation](be18-final-source-validation.json),
[current implementation scope](backend-current-scope.md) and [current manifest](release-manifest.json).
Historical phase evidence below retains its original scope. The full suite is still
failed; later fixture-only passes do not replace it. Production and Dots cutover
remain unauthorized and unqualified.
All observed failures now have [explicit dispositions](be18-failure-resolution.json).
The remaining [host-test modules](remaining-host-tests.txt) require socket-capable
execution and an appropriate outside-checkout temporary root; no guard bypass
or host security change is part of this runbook.

## Meaning of this candidate

This is a durable validation and regression-repair checkpoint, not BE18 release
sign-off. Mandatory local implementation work remains: user-visible local
notifications and their policies, actual application of retained templates, and
output-influence controls, and full-store recovery. These are code gaps, not merely absent live credentials
or unverified deployments. Template creation/storage tests do not prove template
application; record-only monitor intents do not prove user notification delivery.

The machine-readable authority is [release-manifest.json](release-manifest.json),
with exact commands, counts and log hashes in [be18-validation.json](be18-validation.json).
The manifest describes finite local backend behavior. A checked-in implementation,
passing synthetic test, generated TypeScript type or documentation receipt cannot
certify live providers, personal-memory services, frontend journeys or production.

Production and Dots readiness remain false. Sensitive ingestion lacks production
encryption/key-custody qualification. Actual personal-harness result schemas and
credentials are absent. No live connector, browser, voice, remote delivery, model,
hardware, training or empirical-benefit acceptance has been performed. FE13 and
Dots OD00 receipts are absent. Dots source and the frontend are not changed here.

## Freeze and evidence sequence

1. Finish BE15–BE17 integration and freeze runtime code and generated producer
   contracts. Record the source commit and candidate byte inventory digest.
2. Use the already prepared Python 3.14 test interpreter. Do not activate, install,
   download dependencies, supply live credentials or enable live E2E switches.
3. Run the canonical complete default Python suite once. Its default discovery
   deliberately excludes integration, E2E and Docker directories. Run the explicit
   local release slice separately; do not describe the excluded directories as run.
4. Run the critical authority/effect/memory, generated-contract and release-tooling
   gates. Run shared TypeScript typecheck and available workspace checks. Missing
   dependencies or unsupported host lanes are blockers, never a reason to install
   unrequested software or modify unrelated frontend code.
5. Classify every failure against exact preexisting source and a reproducible host
   cause. The BE00 project-tree failures and later AF_UNIX/process-visibility reports
   are leads, not blanket exemptions. Do not waive a new authority, effect or memory
   failure. A failing complete suite stays failed even if baseline causes are known.
6. If code changes to fix a concrete failure, rerun affected checks and record both
   source states. Do not silently replace a failed receipt with a green summary.
7. Generate the manifest from the final validation receipt. The source digest covers
   tracked and nonignored candidate files except documentation/Markdown receipts,
   avoiding circular hashes. Dependency and generated-contract hashes are separate.
8. After the parent publishes the checkpoint, independently verify the exact remote
   commit and its CI status. Local receipts do not establish remote CI success.

The initial campaign used detached backend foundation commit
`0a4621c6b8eb69d170ad4bf5e63aabfb5946b8d0` plus the explicit BE18 files, with the
existing main-checkout interpreter selected through `HERMES_PYTHON`. Import-path
checks established that runtime code came from the detached candidate. Concurrent
frontend work was outside this candidate. The initial 5,331-file run completed with
55,681 passed, 558 failed and 725 skipped; collection/setup errors are additional.
Its source digest was unchanged from start to finish. It remains a failed original
receipt, even after affected repairs and baseline comparisons.

Each exact failing existing Python file was selected for a separate, bounded
comparison against planning baseline `4b7268c69f72c3fa5d2d056a3bbce9a3b65d94cd`.
An unchanged test file is not evidence that production behavior stayed unchanged.
Candidate-only failures are investigated separately from missing dependencies and
reproduced baseline failures. Unknown or unexercised cases remain blockers.

The host maps `/var/tmp` to tmpfs `/tmp`. Some legacy updater fixtures exhaust it
even when run serially, and the original report writer also lost two telemetry
files to ENOSPC. The reporting-only fix writes atomically and records evidence
failure without replacing pytest's true exit status. The release integrity gate
still treats missing receipts as missing evidence. Exact affected reruns may use
pytest `--basetemp` in an owned workspace-backed directory, with that change and
cleanup recorded. Only completed generated fixtures are removed; logs and receipts
are retained, and no unrelated temporary data is deleted.

Canonical full command from the repository root:

```bash
HERMES_HOME=/workspace/shared/ryoko-dev-home \
HERMES_RUNTIME_DIR=/workspace/shared/ryoko-runtime \
HERMES_PYTHON="$PWD/.venv/bin/python" \
scripts/run_tests.sh -j 4 --file-retries 0 -q
```

The runner strips credentials, creates an isolated home and cleans each file's
temporary root in `finally`. Four workers bound concurrent fixture pressure. Watch
free disk/RAM during the campaign; preserve logs outside per-file temporary roots.
Never clean another task's directories to make room. Default per-file timeout and
duration-based scaling remain unchanged. A crash, timeout, collection error, zero
tests or retry-only pass cannot qualify a mandatory gate.

Explicit finite local slice:

```bash
HERMES_HOME=/workspace/shared/ryoko-dev-home \
HERMES_RUNTIME_DIR=/workspace/shared/ryoko-runtime \
HERMES_PYTHON="$PWD/.venv/bin/python" \
scripts/run_tests.sh tests/integration/test_release_slices.py -j 1 --file-retries 0 -q
```

## Evidence map

| Journey / boundary | Concrete evidence | What is not established |
| --- | --- | --- |
| Slice A primary | Owned project APIs; conflicting purposes; explicit missing-harness health; complete immutable Markdown; exact section revision; template; SQLite reopen and resume; revoked grant and approval mismatch | Successful personal-memory recall, generative writing quality or frontend editing |
| Slice A specialist | Built-in per-agent/per-project memory; A→B→A applicability; granted shared artifact bytes; separate sibling memory; revision/template/reopen/resume | Personal-harness access or implicit memory sharing |
| Slice B | Digest-checked original source ranges; two immutable outputs; deterministic mission criteria; reopen before exact human acceptance | Semantic truth of caller-supplied source, live web research, model quality or measured review savings |
| Slice B crash | Owned artifact publication in a real child process; death after local byte acceptance; journal/store reopen; retry refuses; cancellation preserves bytes; read-only effect reconciliation never commits a head or reexecutes | Remote exactly-once behavior or reversal of accepted effects |
| Slice B delivery | Real finalizer/outbox; failed owned transport; store reopen; exact duplicate command; partial then complete artifact acknowledgment; notification retry without execution | Third-party service delivery or human-read confirmation |
| Slice C | Four varied workflow evaluation cases; exact promotion; parameterized output; original workflow digest; real cron monitor baseline/noise/change; duplicate occurrence; reopen; pause/cancel | Live notification or remote action delivery |
| Companion faults | Existing effect reconciler, durable outbox, command replay/control, approval, mission, schedule, scope and executor suites | No coverage beyond each suite's declared local adapter/host |

The retained phase tests are required companions. In particular, delivery failure
and partial acknowledgment remain separate from execution; user steer/cancel have
their owned runtime RPC receipts; stale lease, changed input, consumed approval and
revocation are never interpreted as permission to dispatch. The release campaign
reuses these components and introduces no new agent loop.

## Producer contract and compatibility

[release-contract-fixtures.json](release-contract-fixtures.json) contains synthetic
producer examples, validated against current Python DTOs. The authoritative generated
files are `apps/shared/src/gateway-contract.generated.ts` and
`apps/shared/src/gateway-contract.openrpc.json`; their exact hashes are recorded.
Protocol version 1 binds intent and durable projections, not secret provider state.

A future Dots adapter must first record its own commit plus the exact producer
contract hash and pass OD00. Test authenticated identity, identical retry, changed-key
conflict, disconnect/replay/snapshot-required, cancel, stale approvals, exact artifact
version/digest, partial delivery, schema mismatch and slow-consumer behavior. Reusing
`OPENAI_BASE_URL` is not an AG-UI adapter or proof of ownership integration.

Dots Platform/Slack/voice/headless/schedule routes need the same Hermes mission owner.
There must be one generative loop and one scheduler per mission. A client timeout
does not define mission lifetime. Notification retry cannot rerun inference. Dots
pages remain projections or explicitly coordinated editable artifacts; no global
memory injection or second memory truth is authorized.

## Migration, recovery and rollback

Local reopen/recovery drills demonstrate only temporary SQLite and immutable local
artifacts. They do not demonstrate production key recovery, full-profile encrypted
restore, cross-machine migration or compatible downgrade to pre-schema-36 readers.
The current schema/version and limitations are in the manifest; preserve newer
approval, effect, artifact, mission, workflow, schedule and decision records.

For an authorized future cutover:

1. Pause new admission and both candidate schedule activations. Identify the one
   previous runtime owner from durable records rather than process-name guesses.
2. Drain active claims, inspect unfinished invocations, and reconcile unknown effects
   without replay. Unknown non-idempotent acceptance blocks cutover.
3. Preserve an authorized verified backup of stores, immutable artifacts, policy and
   compatible code/contracts. Retain consumed approvals, delivery attempts, tombstones,
   workflow versions and source pins. Never erase uncertainty to obtain a green state.
4. Validate migration and full restore with the actual profile/key custody on the
   actual supported host before enabling sensitive ingestion or any canary.
5. Fence `runtime_owner`, sync committed projections, and activate only one owner and
   scheduler. Roll back ownership only after the same pause/reconciliation sequence.
6. Disable unsupported new features while preserving readable history. Do not launch
   an older schema-incompatible binary, reset budgets, replay prompts or convert a
   notification retry into mission execution.

No step here authorizes a deployment or credentials. Failed authority, leaked
cross-agent memory, unknown non-idempotent effects or unsupported migrations are
absolute release blockers, regardless of other green tests.
