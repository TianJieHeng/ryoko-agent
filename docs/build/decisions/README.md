# Ryoko architecture decision register

BE00 register, 2 October 2026. The [backend plan](../../../BackEnd_BuildPlan.md)
is authoritative. These are recorded decisions or explicit gates, not implemented
capabilities. Actual IDs, destinations, credentials and hardware remain configuration.

| ADR | Decision / default | Status and owning gate |
|---|---|---|
| 001 | One Hermes runtime owns session/mission commands, effects and schedules; clients project committed state | Accepted architecture; BE02 producer contract, BE18/FE14/Dots compatibility still pending |
| 002 | Only configured primary identity may use the personal external MCP memory harness; stable specialists and ephemeral children use separate built-in stores | User decision accepted; trusted immutable IDs/policy and same-profile enforcement BE01/BE05/BE08 |
| 003 | Stable specialist identity persists independently of execution process. Ephemeral child namespace is tied to that child’s lifecycle and never reused as a specialist | Accepted default; namespace retention and recovery BE08/BE13 |
| 004 | Profile isolation is cooperative application scoping, not hostile-code confinement. Certify one real executor boundary before enabling untrusted execution | Mandatory gate; initial trusted local-owner pilot only, BE05 |
| 005 | Extend existing SQLite leases, SessionDB, projects, goals and generated JSON-RPC contracts instead of creating competing stores or loops | Accepted architecture; transactional command/event semantics BE02, artifacts BE07 |
| 006 | One authoritative artifact byte/version owner, immutable versions, compare-and-swap edits and explicit project grants; personal memory is not project storage | Accepted architecture; BE07 |
| 007 | Unknown external outcome is neither success nor failure; never automatically replay non-idempotent uncertainty. Execution and delivery receipts are distinct | Accepted architecture; reconcile existing gateway and cron policies BE06/BE12 |
| 008 | Stable prompt prefix and opaque provider state survive; bounded fresh corrections or approved compression/new-session boundaries only | Accepted invariant; BE04/BE08/BE16 |
| 009 | Retention/encryption/key custody are explicit deployment gates. No sensitive ingestion certification until restore/deletion evidence exists | Open configuration and proof; BE14 with minimum controls from BE02/BE05 |
| 010 | LAYA supplies typed optional signals under deterministic authority; off/shadow first with independent fallback and per-point promotion evidence | Accepted architecture; BE15/BE16. Hardware/authenticated node unconfigured; training not authorized |
| 011 | Initial intention/DP16 candidate precedes optional Guard breadth. Additional DP05 harness dataset follows real agent integration, using authorized synthetic/redacted data | User sequencing accepted; BE17. No mandatory sixteen-model training or private export |
| 012 | PostgreSQL/fleet/federation/hybrid indexing require measured need; no duplicate Ryoko personal semantic index. Dots implementation stays in its separate repository | Conditional work disabled; BE08/BE14/BE18 and later Dots gates |

## Updating a decision

Append a numbered ADR when a choice materially changes behavior: motivating invariant,
alternatives, chosen scope, data/authority effects, compatibility/migration, actual
verification and rollback. Preserve superseded decisions with their replacement link.
A configured reference is not evidence that a service or executor exists. A passed unit
fixture is not approval to deploy, send data, create credentials or train a model.
