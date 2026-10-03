# BE13 / FE10 configured specialist handoffs

The named-specialist control selects an existing configured identity and executes
one local leaf child through the normal runtime submit pipeline. It does not
create identities, edit configuration, select providers, or enable teams.

## Required existing authority

- An owned strict-identity session using a supported durable runtime transport
- A finite runtime budget and enabled durable local delegation with concurrency 1
- The parent's certified `delegate_task` grant
- A configured specialist role with built-in individual memory and narrowed grants
- A leaf specialist, without a narrowed `delegate_task` grant
- An exact version/digest text methods artifact readable by parent and specialist
- An explicitly selected project readable by both identities
- When present, the current mission must be ready/working in that same project

## Typed control flow

1. `runtime.specialist.catalog` reads configured manifests for an owned project.
   It returns responsibility, exact methods reference, configured limits, narrowed
   tool/project/MCP grants, stable built-in namespace and output schema. Unusable
   configured entries have a denial code. No child or memory store is constructed.
2. `runtime.specialist.preview` validates a bounded objective, exact input/evidence
   references and constraints. Its selection pins the manifest, relevant config,
   parent policy, mission identity/revision, and a five-minute expiry. The digest
   identifies exact input; it grants no authority.
3. `runtime.specialist.handoff` submits the exact selection with stable command and
   idempotency IDs and a runtime revision. It returns the ordinary command receipt.
   Acceptance does not mean launch or completion.
4. The existing admission queue and `TurnFacade` acquire the real command claim,
   session lease, hierarchical budget and mission scope. Live policy, both ACLs,
   methods bytes, selection expiry and the admitted mission witness are rechecked.
   The existing capability broker and `delegate_task` launch exactly one child.
5. Child completion and parent delivery are recorded separately. The bounded
   result enters the canonical parent transcript. Successful child work requires
   parent review: deterministic mission evidence is retained, but this operation
   neither accepts the mission nor starts an automatic parent model continuation.
6. `runtime.specialist.status` is a read-only recovery projection. Keep the same
   command/input identity after an interrupted response. A claimed run with no
   proven current local owner is unknown; inspection never adopts or respawns it.

The generic `runtime.command` input remains narrow and cannot inject a specialist
selection. Internally, the operation remains a standard submit, so existing
snapshot/replay/cancel contracts retain their meanings. No database migration or
new execution loop is introduced.

## Validation and limits

The [validation receipt](be13-named-specialists-validation.json) records the exact
source and logs. Fixtures exercise the real OpenAI SDK with a guarded synthetic
HTTP transport, real agents, on-disk stores, immutable artifacts, child memory,
budget accounting, mission verification, owned RPC admission and reconnect.

This does not qualify a live provider, installed connector, remote executor,
hardware setup or team productivity benefit. Teams remain disabled. A newer
methods head cannot silently replace the explicitly pinned immutable version;
missing/changed pinned bytes, revoked ACLs and altered configured manifests fail
closed before launch.
