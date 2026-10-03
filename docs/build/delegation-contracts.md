# BE13 durable specialists and local executor contracts

## Supported boundary

The existing `delegate_task` tool still constructs and runs children through the
existing `AIAgent`/thread-pool/async completion lifecycle. Strict identity profiles
now require a configured durable admission adapter and inherited BE03 budget.
Legacy profiles retain their existing behavior. No second agent scheduler or
provider loop was introduced.

The stable configured specialist ID, ephemeral child UUID, running process and UI
subagent label are different identities. A configured specialist uses its stable
BE08 individual-memory namespace across sessions. Ephemeral children and their
descendants get separate namespaces. Neither receives Ryoko's personal MCP
servers or personal credentials. The existing identity intersection, provider
recipient grants and BE05 isolation gates still apply.

Strict delegation configuration under `delegation`:

```yaml
durable:
  enabled: true
  limits:
    max_depth: 1
    max_total_children: 1
    max_concurrent_children: 1
specialists:
  configured-specialist-id:
    responsibility: Analyze the supplied authorized source
    methods_ref:
      id: exact-registered-methods-artifact
      version: 1
      sha256: operator-selected-64-character-artifact-digest
    limits:
      max_depth: 1
      max_total_children: 1
      max_concurrent_children: 1
    output_contract:
      type: object
      properties:
        answer: {type: string}
      required: [answer]
```

The specialist ID must already be a configured `agent_identity.agents` specialist.
The methods reference must resolve to current bounded text/Markdown bytes through
both the parent and specialist's live project ACLs and the parent's mission scope.
The placeholder above is intentionally not a valid configured digest. No sample
credential, executor attestation, live destination or invented specialist name is
installed by this change.

Task inputs may name `specialist`, exact `artifacts`/`evidence` reference arrays
(`id`, `version`, `sha256`) and bounded `constraints`. Evidence digests cover the
canonical retained anchor projection excluding the derived `effective_validity`
and `grants_execution` fields. Evidence is rechecked for current validity. Source
references cannot provide identity bindings, executor attestations or budgets.

## Durable authority and execution

Schema 41 adds bounded `delegation_roots` and `delegation_handoffs` records on
SessionDB's canonical writer. The immutable handoff records objective,
constraints, original root/parent/child identities, exact source versions,
narrowed grants, deadline, workspace manifest, executor reference, output schema
and the existing budget account plus admission reservation. A one-millisecond
host-overhead reservation is distinct from descendant physical provider/tool
reservations; the latter still atomically charge every ancestor's original
finite limits. It is not a fabricated full-task cost prediction.

Root aggregate fan-out and active-child caps are transactional and survive
continuations/restart. Uncertain work keeps an active slot. Expired budgets,
changed root limits and stale owner generations reject admission. A canonical
constructor-bound child session is required before admission. Every live start
is one-shot and rechecks its original parent writer generation, exact identity,
source ACLs/mission, input digests, specialist manifest and executor placement.

The staging directory is not a sandbox. Only accepted immutable project artifact
bytes are copied into the child's own namespace. Strict children never inherit
ambient parent cwd, file-read cache or a shared terminal container alias.
`execute_code` creates a fresh output workspace from those exact input files and
still runs only through the BE05 isolated Linux computation adapter. Publication,
merge/replacement and export remain the existing BE07/BE11 exact-approval,
version/section conflict and recipient-bound mechanisms. No automatic merge or
promotion is implemented here.

Local executor attestations are process-local host records bound to the actual
PID/start fingerprint, principal/profile/home, capabilities and generation.
Caller-supplied serialized references confer no authority. The `python.isolated`
capability is added only after the actual namespace/chroot/seccomp adapter probe
succeeds. Unavailable confinement denies execution without a legacy fallback.
The currently supported location is local; cost is explicitly unpriced. The
60-second placement ticket is short lived and is rechecked at dispatch. A
restart/disconnect/expiry blocks the original placement. No private work is
silently migrated.

## Completion, parent synthesis and recovery

The child completion receipt is immutable and separate from a parent delivery
claim. Existing async `task_json`/events carry canonical handoff references.
Strict async dispatch cannot replace an existing unit or accept unbound handoff
references. Strict completion persistence failure does not downgrade to an
in-memory-only delivery.

The existing CLI/TUI completion consumers pass their trusted parent context.
Missing/wrong-owner claims fail closed; a forged event session label is not a
credential. Claim/ack updates the existing async ledger and linked handoff rows.
Legacy shortcut acknowledgments cannot bypass strict claims. Synchronous results
are also recorded durably; absent a separate explicit consumption acknowledgment,
the handoff's parent-delivery state remains pending rather than claiming that a
UI/client received the result. The parent retains synthesis and user-facing
status responsibility.

A parent writer can classify abandoned accepted work as `orphaned` and running
work as `unknown`. Completed siblings remain recoverable. Existing abandoned
async-owner recovery updates strict records and does not inspect ambient git
cwd or unscoped transcript tails for strict tasks. It never blindly reconstructs
a running child. No process checkpoint/resume or live supervisor adoption adapter
is certified. `execution_resumed` is always false on completion recovery.

## Conditional acceptance and rollback

The finite single-child path is implemented and locally regression-tested.
Parallel strict team admission is deliberately blocked with
`delegation_team_unqualified`: no live paired specialist/team-vs-single-agent
benchmark was authorized or executed. Declaring a larger configured concurrent
limit does not invent qualification. Hardware/device routing beyond the current
local process, external services/channels, real STT/TTS adapters, OCR and arbitrary
screen/OS control remain unsupported/unconfigured. See
[media-service-contracts.md](media-service-contracts.md) for the two real finite
local document services, explicit PTT interfaces and selected-frame workflow.

Disable `delegation.durable.enabled` to stop new strict admission. Pause/stop live
children through existing exact-owner controls; preserve handoffs, budget/effect
uncertainty, completion claims and original memory namespaces. Do not delete
schema-41 records to make an older binary appear compatible. Native macOS/other
OS capability lanes and live team/hardware/media qualification remain separate
release gates. BE13's full measured-benefit exit criterion remains pending.
