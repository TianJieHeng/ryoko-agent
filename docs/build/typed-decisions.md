# BE15 typed decision plane

## Implemented and intentionally unqualified

This change provides a non-generative typed protocol, sixteen versioned decision
point contracts, point-specific modes, compact immutable packets, redacted
receipts, bounded independent inference/circuit capacity, pinned mutual-TLS LAN
transport, a transport-neutral service handler, metadata health validation, and
local calibration/benchmark tooling. It does not install or download LAYA or any
model. The third-party LAYA adapter/plugin belongs in a separately maintained
plugin repository, not under core `plugins/`.

The initial experiment remains Router intention routing / DP16. Repository search
found no actual intention-routing dataset. The checked-in dataset is explicitly
synthetic arithmetic/test material, not that dataset, trained weights, or model
quality evidence. No Jetson, JetPack, PyTorch/ONNX/TensorRT, serving binary, remote
firewall, deployed credentials or latency target has been qualified. Guard remains
optional future scope. Catalog coverage is neither sixteen models nor a rollout
prerequisite.

## Contract and ownership

`agent.decisions.registry.REGISTRY` records owner, family, version, closed
questions, explicit `unclear`, state fields, threshold, fixture reference, safe
incumbent and actual consumer availability. `contract_digest` covers question
wording/options and this metadata. Changes require renewed evaluation.

`DecisionClient.decide(point_id, state_packet, contract_version, deadline,
question_id=..., live_options=...)` uses a UTC deadline, never a chat-completions
request. `StatePacket` freezes canonical JSON and scope digest. Fields are
point-specific, at most 16 KiB, bounded depth/text/menu size. Text is untrusted
data, never an instruction source. There is no whole-transcript field.

Every response is bound to request, point, version, question, input and scope
and to pinned model, calibration and service digests. The exact closed option
set must have finite nonnegative probabilities summing to one. NaN, booleans,
unknown options, selection/argmax inconsistency, unclear mismatch, extra fields,
model swaps and contract/scope mismatch all fail to the incumbent.

Dynamic menus come from the caller's authorized live menu and include `unclear`.
DP16 tool selection additionally requires the preceding selected family; tool
verification requires the selected tool to belong to that live menu. The current
front-door consumer observes L1 only. BE16 owns useful multi-stage planning,
cache-safe schema changes, always-authorized search/reopen and its matched task
outcome trials. No BE15 observer changes a frozen prompt, history or schema.

## Modes and real consumers

The `decisions` configuration is empty/off by default. Example structural shape
(the digest values must name real separately qualified artifacts):

```yaml
decisions:
  schema_version: 1
  bundle:
    model_digest: '<64 lowercase hex characters>'
    calibration_digest: '<64 lowercase hex characters>'
    service_digest: '<64 lowercase hex characters>'
  points:
    DP16: {mode: shadow, threshold: 0.95, timeout_seconds: 0.15}
```

Each point can independently be off/shadow/advisory. Optional `effect_thresholds`
is a map from closed option to required confidence; receipts retain the complete
threshold map and promotion gates bind it. SDK enforce additionally
requires a nonexpired gate bound to the exact point, contract, model,
calibration, thresholds and independent evidence plus a durable receipt sink.
The only implemented gate effect is a qualified recommendation. Production
configuration rejects enforce until a point-specific authoritative consumer is
qualified. Neither a gate nor classifier can mint tools, approvals, identity,
budgets, egress permission, memory access, or override mandatory floors.

Production core lifecycle observation is opt-in through the existing hook
consumer, not a test-installed callback:

- DP16: `pre_api_request`, first physical attempt of the first API call only;
  current user text and live tool names, never the system prompt/history
- DP06: real `pre_tool_call`, observation of the destructive question only
- DP07: real `transform_tool_result`, observation of content class only;
  existing tool result bytes and plugin precedence are preserved
- DP10: `background_review.spawn_background_review_thread`, core observation;
  review counters/authorization remain authoritative
- DP11: `cron.durable_sources.observe_sources`, owner-bound immutable source
  metadata only. This does not semantically score inbox relevance, decide
  notifications, or observe a live inbox. The finite monitor remains unchanged

The other catalog points have no BE15 consumer. An external Route-A plugin may
observe existing tool hooks but cannot claim DP10/DP11 core coverage. It must
return no pre-tool directive and no replacement tool result in shadow.

Core observation requires the real admitted identity/run/lease, uses its
remaining deadline and reserves/settles measured wall time in the existing budget
ledger without consuming main-provider attempts. Optional budget admission
refusal skips the observation without setting the main retry controller. Legacy/unbound sessions do not infer ownership. Current
BE14 `privacy_qualification()` is false, so all private production packets create
privacy-blocked metadata receipts without network transmission. Even after that
qualification changes, a destination-bound private authorization implementation
is still required; there is no config boolean to bypass it. Synthetic/public SDK
packets exercise the service protocol only. Do not relabel private data synthetic.

## Failure and receipt behavior

Inference has a point-local circuit, finite worker slots, short deadline, and no
retry. A hung worker retains its slot; no unbounded replacement threads are
created. Late results cannot be applied. Receipt writes also share finite slots
and a bounded wait. These paths never enter main-model retry or fallback code.

`decision.observed` reuses the BE02 fenced journal. It stores the complete valid
distribution, options, input/scope digests, bundle/contract digests, thresholds,
mode, actual route, incumbent/fallback and measured node/client latency, without
raw state, endpoint URLs, credentials, source text or arbitrary exception strings.
`decision.outcome` binds a later closed-option label and bounded outcome to an
existing same-scope receipt plus label provenance digest. Model self-confidence
is not an outcome label. Hashes and option names remain private operational data.

Shadow/advisory receipt outage leaves the incumbent untouched. Enforcement fails
to the incumbent unless the mandatory durable receipt is confirmed. A timeout
can leave an optional late journal append; inspect the receipt ID, never assume
an unavailable sink means no record exists. BE14 checkpoint/retention controls
own this existing journal; no independent forever-growing raw-state log is added.

## Evaluation

`evals/decisions/contract_fixtures.json` exercises every registered question with an
explicit synthetic `unclear` fallback. This establishes protocol coverage, not
classification quality. It is separate from the arithmetic calibration fixture.

`evals/decisions/evaluate.py` computes deterministic accuracy, multiclass Brier,
ECE, confidence-vs-correctness AUROC, descriptive coverage at target precision,
fallback rate and asymmetric candidate error cost, grouped by point/question/
option count. Provenance pins frozen holdout, label source and split identities.
Undefined metrics remain explicit, and held-out threshold coverage is not a
promotion gate. See its README for all populations and caveats.

Rollback disables each point independently and restores model, contract,
calibration and threshold policy as one bundle. Question wording, option count,
calibration or model changes invalidate the old gate. Keep the incumbent intact.
