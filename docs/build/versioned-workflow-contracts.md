# BE11: evaluated, versioned manual workflows

## Supported vertical slice

`agent/workflow_contract.py` defines immutable, bounded JSON versions. `hermes_state_workflows.py` stores executable content, lifecycle, templates, evaluations, correction/failure evidence, human decisions and run pins in the existing SessionDB writer. Schema 39 adds tables without rewriting prior mission/effect/approval records.

The installed interpreter supports parameterized Markdown and BE10's finite local domain producers. It is not a shell, browser automation engine, model tool, arbitrary Python importer or second agent loop. A semantic Markdown instruction remains literal output text. Coordinates, browser click actions, undeclared capabilities, arbitrary imports and unknown environment manifests are rejected. Interpolation is nonrecursive and byte-bounded before joining; expanded domain arguments are bounded before serialization.

P09 is exercised through the real owned JSON-RPC transport: create a draft, evaluate varied inputs, inspect an exact promotion, approve it, prepare and review outputs against an exact Mission, then run a second input using the same unchanged version.

## Immutable content and lifecycle

A WorkflowVersion carries `workflow_id`, positive `version`, `project_id`, closed input schema, acyclic dependent steps, output expectations, finite capability requirements, `local_deterministic_v1` environment, provenance, exact predecessor and optional template pin. JSON-backed frozen objects return detached copies. The canonical JSON digest covers all executable fields. Editing creates a new version with its exact earlier predecessor; no state-changing API edits canonical bytes.

Lifecycle metadata is separate:

- `draft` becomes `tested` only after retained interpreter-produced evaluation evidence passes
- `tested` becomes `approved` only through a separately prepared, exact owned human decision
- `approved` may become `deprecated`; draft/tested/approved/deprecated may become `revoked`
- Revoked versions cannot start or publish work, and pending workflow records are paused; existing artifacts/effects are never deleted or replayed
- Rollback changes only the active pointer to an approved ancestor, preserving content, evaluation, run history and successor records

Decision approvals use the existing durable approval records. They bind content digest, evaluation reference, lifecycle revision, active-pointer revision, policy, session holder/generation and sharing destination when relevant. Consumption and lifecycle mutation commit together. An admitted model RuntimeRun cannot call the owned-control APIs to promote itself, even through a genuine attached transport.

`resolve_executable(context, db, project_id=..., workflow_id=..., version=..., sha256=...)` is the shared admission consumer for later schedulers: require an exact approved canonical version and live project read/write authority. A moving `active_version` is never a resume input. Any later consumer must use its own admitted runtime and existing policy/budget/effect checks; resolution does not mint them.

## Evaluation and provenance

`runtime.workflow.evaluate` takes at least two distinct `tuning` and two distinct `held_out` inputs, exact expected final-output SHA-256 labels and immutable recorded generalist-baseline artifacts. The actual interpreter executes each case locally without artifact publication or external mutations. Evidence retains case manifest and input/output digests, final-output pass/fail, candidate elapsed time, baseline comparison and, for successor versions, the same cases against the exact incumbent. Known inputs cannot move between tuning and held-out splits across versions.

Expected labels and generalist outputs are explicitly human supplied; their semantic correctness and independent origin are not host-attested. Held-out separation is enforced for recorded inputs, not claimed as proof that no outside author saw them. No model/weight training, live generalist inference, hosted A/B account mutation or measured user-cleanup savings is claimed.

Accepted-work drafts require an exact accepted, still-verified Mission revision in the same project. Demonstration drafts require immutable recording references plus a structured unexpired consent artifact naming the exact project, recordings and `workflow_extraction` purpose. Demonstrations provide evidence, not inherited authority. Source-derived procedures retain `private_derived=true`.

`runtime.workflow.feedback` retains correction/failure artifact references without changing procedure content. Templates are distinct immutable style/section metadata; versions pin one exact template. Template edits do not rewrite workflows or deliverables, and style prose is not executed. Automatic personal-harness procedural discovery remains unenabled; the project list API exposes only explicitly granted canonical project procedures and never reads personal memory.

## Owned API sequence

1. `runtime.workflow.create` / `runtime.workflow.template.create`: canonical draft or style definition
2. `runtime.workflow.get` / `runtime.workflow.list`: exact content/lifecycle or granted project discovery
3. `runtime.workflow.evaluate`: retained candidate/incumbent/recorded-baseline evidence
4. `runtime.workflow.decision.prepare` then `.commit`: `approve`, `deprecate`, `revoke`, `rollback`, or `authorize_export`
5. `runtime.workflow.run.prepare`: exact approved version, parameters and existing ready/working Mission revision
6. `runtime.workflow.run.publish`: ordered exact output approvals; individually idempotent artifact effects, manifest last
7. `runtime.workflow.runs`: immutable run pins, published references and authoritative control status

Run pins retain workflow digest/version, original parameters, template and environment, original admission clock, Mission identity/revision and budget root. Reopening the same live claim regenerates only the same bounded local outputs and compares the output digest. Changed inputs or recipe are rejected. Expired/replaced claims are not silently adopted; inspect existing artifact/effect receipts and reconcile them. No browser/external mutation is retried as a workflow step.

Mission completion is separate from artifact publication. Published references are attached to the existing Mission; BE09 still owns deterministic verification and human acceptance. Configured budgets reserve executor slots and bounded wall units through the existing root ledger, settle actual local work and retain any overrun as debt. Without configured budget policy this remains finite local computation, with input/output/step bounds and the existing control deadline.

Manual stop/review uses the existing `runtime.artifact.cancel` and `.status` with the workflow command ID. Cancellation stops the owned claim, is visible in run history and never claims to undo published effects. Revocation from another owned conversation in the same profile blocks a prepared run before publication. Scheduling, pause-before-scheduled-revocation integration and background occurrence handling belong to BE12.

## Private export and limitations

All canonical export requests require project `share` authority and an exact `authorize_export` human decision naming recipient and immutable version. A different recipient, changed content or revoked version is denied. Export returns a bounded package; it does not transmit it to that recipient. Actual external publication still needs its transport's authority and policy.

The callable CLI `python -m hermes_cli.workflows definition.json` only validates syntax and prints its digest, explicitly `executed:false, approved:false`. Runtime work goes through owned controls. No graphical canvas or frontend behavior is added; TypeScript/OpenRPC contracts are generated for future FE03/FE06/FE07 consumers.

## Reproducible validation

Use the already provisioned independent interpreter:

```
HERMES_PYTHON="$PWD/.venv/bin/python" scripts/run_tests.sh \
  tests/agent/test_workflow_contract.py tests/agent/test_workflow_runtime.py \
  tests/tui_gateway/test_workflows_rpc.py tests/hermes_cli/test_workflow_authority.py \
  tests/hermes_state/test_workflow_migration.py tests/hermes_state/test_mission_records.py \
  tests/tui_gateway/test_domains_rpc.py tests/tui_gateway/contracts/test_generated.py
npm run typecheck --workspace @hermes/shared
```

No dependency installation, live services, private-source training, external publication or hardware provisioning is needed by these checks.
