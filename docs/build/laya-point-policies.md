# BE16: independent point policies and recoverable DP16 planning

## Current integration plan

The [LAYA integration build plan](../../LAYA_Integration_BuildPlan.md) adds the implementation sequence for the supplied systemone runtime, bounded context and catalog prompts, native batching, multi-tool selection, destination authorization, hardware qualification, and cache-safe ownership. It is a planning update, not activation or release evidence. The existing foundation behavior below remains unchanged.

## Shipping behavior and evidence boundary

Current direction: LAYA experiments and activation are paused. This checkpoint
continues foundation-only implementation and offline verification.

The shipping default remains `decisions: {}`: all points are off. Explicit
BE15 `shadow` or `advisory` configuration adds observations only. Production
configuration still rejects `enforce`; no real model release is qualified.
Private packets remain blocked by the BE14/privacy and destination-authorization
boundary. No training, downloads, paid providers, live private packets, node
provisioning, credentials, or activation were performed for this phase.

All sixteen point contracts have deterministic floor adapters and synthetic
fixtures. This does **not** mean all sixteen have a live owner consumer:

| Point | Shipping owner integration | Floor under a future qualified recommendation |
| --- | --- | --- |
| DP01 | Adapter only; ingress integration pending | Direct messages/questions always allowed; only opted-in ambient traffic can skip |
| DP02 | Adapter only; busy-input integration pending | Preserve slash-command cancellation/steer semantics and late/missed status |
| DP03 | Adapter only; provider routing pending | Intersect capability, privacy, grant and cost routes; main fallback or pause |
| DP04 | Adapter only; skill/tool owner pending | Live authorized candidates only; authorized default view fallback |
| DP05 | Adapter only; recall owner pending | Personal/both recall only for authorized Ryoko primary; scoped fallback |
| DP06 | Real `pre_tool_call` observer | Existing deterministic block and mandatory confirmation always win |
| DP07 | Real `transform_tool_result` observer | Untrusted wrap/flag/quarantine retains original evidence and source digest |
| DP08 | Adapter only; regex owner pending | Classify regex hits only; unclear/instruction blocks; descriptive relaxation needs approved allowlisted class |
| DP09 | Adapter only; verify owner pending | Required tests, validation, artifacts and receipts must exist before done |
| DP10 | Real bounded background-review observer | Counter backstop remains; individual review never inherits personal harness |
| DP11 | Real durable-source metadata observer | Notify/digest/ignore only; never reply or create commitments |
| DP12 | Adapter only; outbound owner pending | Hold original text; never auto-edit; shadow outage preserves existing send policy |
| DP13 | Adapter only; progress owner pending | Deterministic repeat/budget stops win and retain usable partial result |
| DP14 | Adapter only; compaction owner pending | Protected exact identifiers, approval bindings and regex anchors are retained |
| DP15 | Adapter only; specialist dispatcher pending | Live authorized roster only; default or human on uncertainty |
| DP16 | Real front-door planner observer and bridge diagnostics | Authorized tool bridge survives no-tools; schema installation still unqualified |

The readiness inventory is also available through
`agent.decisions.point_policies.production_status()`. A tested pure adapter is
not an enforcement consumer. The owning subsystem must supply trusted facts,
recheck permissions at dispatch, and obtain its own real release proof before
applying an effect. FE12 can replay closed plan/policy/miss metadata; UI controls
and authenticated operator promotion endpoints remain separate work.

## Initial experiment: DP16, not sixteen models

The first experiment remains front-door intention/tool routing. A checkout-only
filename inventory found no existing intention-routing dataset. Its actual
schema, label coverage, license, train/calibration/holdout separation and quality
therefore remain unverified. The synthetic BE15/BE16 fixtures are not a substitute
or a checkpoint. DP10/DP11/DP09 are later candidates; a DP05 harness dataset is
post-agent-build work. No sixteen-model or sixteen-dataset prerequisite exists.

The future authorized experiment is: inspect the actual supplied dataset;
identify coverage gaps; add authorized synthetic family/live-menu/unclear/reopen
fixtures where useful; train away from serving; freeze independent calibration
and holdout splits; evaluate; run live shadow; consider point-specific promotion.
A failed gate stops at shadow. No real checkpoint, empirical task-success claim,
calibration achievement, latency measurement or net savings is asserted here.

## Promotion and rollback

`PointRule` contains a point ID, mode, class thresholds, timeout, bounded effect
categories, and exact rollout-scope digests. It references the BE15 closed
contract and incumbent fallback. `PointPolicyBook` records a durable transition
before applying it. A host-supplied operator verifier must approve the exact
point/evidence/policy tuple. Public config cannot manufacture an approval.

`inspect_release` reports every unmet requirement rather than a global enable
flag. It binds contract/model/calibration/service/policy/scope versions; requires
frozen and disjoint holdouts, independent labels, real-candidate provenance,
calibration/holdout/red-team/live-shadow/latency/budget report digests and bounded
metrics; rejects synthetic fixtures, split overlap, expiry and wrong approvals.
DP03/DP16 additionally need paired outcomes and cache-cost reports. Each point
has its own adverse-outcome criteria, e.g. missed direct requests, false allows,
missed durable facts, lost anchors, or important-item recall.

The default numerical checklist (including a 500-shadow-receipt collection
floor) is an initial conservative checklist, **not** a statistical sufficiency
theorem or a verified deployment target. Operator review must validate provenance,
representativeness, label independence and confidence bounds. A hash proves
binding, not report authenticity. The release API has no permission authority.

An enforcing resolution also requires a current qualified release, a persisted
BE15 decision receipt, matched bundle pins, an allowed effect category and the
point's deterministic floor. A correction/override only changes a closed option;
it cannot create tools, permissions, recipients, budgets or missing evidence.
Shadow/advisory resolutions preserve the incumbent effect.

Rollback records one point's reason (`drift`, `false_allow`,
`missed_direct_request`, `stale_menu`, `tool_recovery_failed`, budget or latency
regression, or operator action) and returns it to shadow/off. Other points are
unchanged. A prior evaluated release can only be restored as its complete matched
bundle with renewed expiry/operator checks. Effects are not replayed. Restart
is conservative: off until the trusted host revalidates the entire release.

## DP16 protocol and cache contract

1. Read only authorized live metadata from the owner's established discoverable
   snapshot, plus currently exposed schemas. Do not rediscover/probe servers.
2. Logically batch independent L1 need, L2 effort and L3a family yes/no questions.
   BE15 still sends one closed question per request with bounded concurrency and
   one shared deadline; this is not a claim of a deployed batch inference API.
3. For selected families only, ask L3b live-tool choices. Tool-dependent
   verification follows those choices. Catalog/scope changes fail to the full
   authorized incumbent. Contradictory no-tools/family results also fall back.
4. A no-tools bundle contains the already-authorized `tool_search`,
   `tool_describe`, and `tool_call` escape. An unavailable bridge prevents
   filtering. Profiles lacking bridge grants retain the BE15 need-only observer.
5. `BundleSession` only installs at a new compatible prompt context or permitted
   compression boundary. Repeated turns in the same context retain identical
   schema bytes. A new task ID alone is not a cache boundary. This component is
   tested but is intentionally not installed in the production prompt pipeline
   before empirical qualification and a real owner consumer exist.
6. Reopening describes an authorized tool as tool-result data; it never rewrites
   the schema prefix. Live policy/catalog revocation denies stale tools. Actual
   calls still go through the existing broker/approval/credential checks.
7. Effort/tool-count hints neither hard-cap a task nor raise any limit. Planner,
   fallback and recovery wall time belong to the same task deadline.

The live `tool_describe` seam records candidate omissions without modifying its
results. In shadow/advisory these receipts explicitly set `observation_only`:
these are hypothetical planner misses, not deployed-filter recovery success.
Denied names are digested and never exposed as cross-agent discovery metadata.

## Evaluation and replay

`planner_evaluation.evaluate_planner_pairs` accepts matched offline trials. It
reports no-tools precision, needed-tool recall, planner-miss/recovery rates,
paired task success, prompt/schema/bridge/recovery tokens, cache hit/miss billing
components, total cost, first-response latency and full-task latency. Empty
populations yield undefined metrics, not perfect scores. Cache hit/miss counts
are a billing partition, not extra tokens. Descriptive reports never qualify a
release and do not synthesize missing confidence bounds.

`decision.tool_plan`, `decision.policy`, and `decision.planner_miss` are closed
owner-bound replay payloads. Unknown fields fail closed in the projection. They
retain scope/version/receipt bindings and explanations, never packet bodies,
source text, secret errors, credentials, or denied tool names. Production logs
continue to use the existing governed journal, not a parallel private payload log.

The focused validation receipt and remaining empirical/owner gates are recorded
in `docs/build/be16-validation.json`. Unit fixtures, a real admitted mocked-SDK
AIAgent turn, A→B→A owner replay, and authorized bridge recovery are distinct from
hardware or real candidate evaluation. All configuration remains unchanged.
