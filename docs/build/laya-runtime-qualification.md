# LAYA runtime qualification: blocked

Recorded 2026-10-05. Integration base: `18aa40685b7a2129f939ea5e338163293eb809af`.
Code and local synthetic tests are not real-host or model-quality qualification.
Production remains off. See [readiness](laya-integration-readiness.json),
[the synthetic inspector exercise](laya-synthetic-qualification.json),
[the operator runbook](laya-systemone-runbook.md), and [the build plan](../../LAYA_Integration_BuildPlan.md).

## Actual evidence versus fixtures

- L00–L06 implemented and locally tested the authorized catalog, context, codec,
  staged planner, HTTPS boundary and real owner/receipt observer path
- Local TLS tests generated their own certificate and fake credentials; inputs,
  probabilities and outcomes are synthetic. They verify integration invariants,
  not LAYA accuracy, calibration, hardware or end-to-end network performance
- Operator-reported HTTP 200/401, `i1` ONNX fp16, boot-start tunnel and login-start
  model remain reports. No real API key was read, installed or transmitted
- One ordinary unauthenticated cloud GET to the supplied `/health` route at
  2026-10-05 10:33:25.907 UTC returned HTTP 403, application/json, 708 bytes.
  Normal TLS verification and a 10-second deadline were used. No credentials,
  redirects or additional routes were used. An earlier public-web reader could
  not fetch the URL. The 403 source is unverified: it may be the service or an
  intermediary. This is not evidence that the Jetson is down, nor qualification
  from the actual Ryoko host. Raw response content was not published

## Missing operator evidence

1. Secure user-controlled `LAYA_API_KEY` provisioning and exact identity/recipient
   grants on the machine running Ryoko; no key in chat or the repository
2. Real Ryoko-host synthetic authenticated inference, invalid/missing/revoked
   authentication, exact route isolation and restricted/authenticated origin
3. Actual loaded model/checkpoint, tokenizer/export, calibration and serving
   identity with artifact provenance and license; configured hashes are insufficient
4. Warm/cold whole-plan p50/p95/p99, queue/memory/temperature/power under actual
   concurrent load, timeout/outage recovery, and boot without desktop login
5. Audit of Cloudflare edge/origin/local activity/logs/traces/crash retention,
   deletion/restore and key custody; exact private-data category/destination consent
6. Independent labels and outcomes, frozen disjoint training/calibration/holdout
   episodes, languages/follow-ups/multitool coverage, and actual reduced-bundle
   recovery plus net token/cost/cache/latency evidence
7. Explicit approval of the exact passing release, scope, effect, rollback and
   observation window before any production rollout

Those are real completion gates, not TODOs that synthetic tests can waive. No new
model download, training, paid compute, tunnel/server modification or private
Memory Harness export was performed. The existing candidate must be evaluated
before proposing additional data/calibration/training.

## Offline report tool

`python -m evals.decisions.qualify_laya <redacted-evidence.json> --readiness docs/build/laya-integration-readiness.json --output <new-report.json>`

Use the existing supported interpreter. The output must not already exist; exit
0 means inspection succeeded, not qualification. The tool has no credential,
provider, transport or network path. Its closed input schema is demonstrated by
`evals/decisions/laya_qualification_fixtures.json`. Input identifiers are digests,
not raw task text or source documents. It rejects duplicated episodes/batches,
split leakage, nonfinite/bool metrics, missing units and contradictory accounting.

Accounting includes classifier input/output and cost once per batch; complete
planner context/admission/network/stage/receipt time; task output, schema, bridge,
recovery and cache costs. Unknown usage stays unknown. A shadow omission is not
an observed reduced-bundle recovery, and uncertain remote completion cannot pass
latency/cost qualification.

The report reuses the existing descriptive paired evaluator without changing
`qualifies_production: false`. Statistical estimates are independent-episode
Wilson, conservative bounded-mean/count-ratio/latency and DKW bounds with explicit
predeclared finite bounds and multiple-comparison adjustment. Fixed-bin ECE uses
a one-sided McDiarmid bound: Jensen bounds population binned ECE by the expected
empirical L1 residual statistic; changing one independent episode changes that
statistic by at most 2/n. This is distinct from model confidence.

These methods assume independent representative episodes, a frozen predictor and
acceptance rule, fixed calibration bins and predeclared population bounds. They
may remain inconclusive with perfect observed recall/recovery; the conservative
count-ratio method and sample cap are a documented measurement limitation, not
permission to weaken the 0.99 gates or treat tools within an episode as independent.
Saved assertions and hashes cannot authenticate labels, hardware, consent or
operator approval. All external verification requirements remain visible.

## Current result

The committed synthetic inspector exercise reports 35 blockers and explicitly:

- `qualifies_production: false`
- `L07_complete: false`
- `L09_release_qualified: false`
- `L10_authorized: false`

L07 is blocked, with offline tooling complete. L08 implementation can be tested
independently behind qualification. The combined local campaign can establish
code invariants while L09 real-candidate evidence and L10 rollout remain blocked.
