# Offline decision calibration measurements

This BE15 scaffold measures supplied predictions. It does not fit a model or
calibrator, run inference, load a checkpoint, call a service, change configuration,
install thresholds, or authorize enforcement. It imports only the standard library.

The bundled holdout is **hand-authored synthetic metric arithmetic**. It is not the
existing user intention-routing dataset, an inspection of that dataset, a trained
Router/Guard, or evidence of model quality. Its model/calibration/contract digests
identify synthetic descriptors, not deployed artifacts. Its calibration split is
an explicit empty placeholder; there has been no training or calibration. The
illustrative DP06 rows do not implement or enable optional Guard behavior.

Run from the repository root:

```bash
.venv/bin/python evals/decisions/evaluate.py
HERMES_PYTHON=$PWD/.venv/bin/python scripts/run_tests.sh tests/agent/test_decision_calibration.py -q
```

The CLI reads only the bundled `synthetic_holdout.json` and prints deterministic
JSON to stdout. `--bins` (default 10) and `--target-precision` (default .95) control
descriptive reporting. There is deliberately no dataset-path, training, network,
or promotion option. The fixture contains `schema_version`, `notice`, `rows`,
`provenance`, and `error_costs`.

## Python API and version-one input

`agent.decisions.calibration.evaluate(rows, *, provenance,
target_precision=.95, bins=10, error_costs=None)` returns a JSON-safe dictionary.
`EvaluationError` is a `ValueError` for rejected inputs. Inputs are not mutated.
All objects use exactly the fields below; no raw state packets are accepted.

Each row in the JSON list has:

- `row_id`: unique observation ID; `episode_id`: stable source/task episode ID
- `point_id`: DP01–DP16; `question_id`: versioned question identity, bound by the
  contract digest; `split`: exactly `holdout`
- `options`: 2–128 unique closed option strings, including the literal `unclear`
- `distribution`: object containing exactly every option, including zero masses.
  Probabilities must be finite numbers (not booleans), each in [0, 1], summing to
  one within absolute 1e-9 tolerance. They are never silently renormalized
- `label`: independently obtained ground truth, belonging to the closed options
- `fallback`: explicit boolean recording whether the incumbent fallback was used

A missing/unavailable distribution is represented by JSON `null` **only** with
`fallback: true`. Never invent a uniform distribution to hide an outage or pass
an invalid distribution as valid. A valid prediction may also have fallback true
(e.g. an incumbent route retained during shadow or a low-confidence decision).
Each row remains in the fallback and coverage denominators. No selected-choice
field is trusted: candidate top-1 is computed from the full distribution; ties
use the lexicographically smallest option. `unclear` remains a scored class.
Identifiers/labels must be nonempty strings of at most 256 characters. At most
100,000 rows are accepted; an empty holdout produces explicit undefined metrics.

`provenance` has exactly these fields:

- `dataset_id`, `domain`: nonempty identities of the frozen domain holdout
- `dataset_sha256`: canonical digest described below
- `split: "holdout"`, `frozen: true`, and explicit boolean `synthetic`
- `label_source`: `human`, `independent_teacher`, `outcome`, or `synthetic_fixture`.
  `synthetic_fixture` requires `synthetic: true`; synthetic inputs may also have
  independently obtained human/teacher/outcome labels. Candidate self-labels are
  not an accepted provenance class
- `model_digest`, `calibration_digest`, `contract_digest`: pinned artifact SHA-256
  digests for a real evaluation (synthetic descriptors in the bundled fixture)
- `calibration_dataset`: a split manifest with exactly `dataset_id`,
  `dataset_sha256`, and `episode_ids` (a list of unique strings)
- `training_datasets`: a list, possibly empty, of the same split manifests

Digests are 64 lowercase hexadecimal characters. Holdout, calibration and all
training datasets must have pairwise-disjoint dataset IDs, digests and episode
sets. Holdout episodes are derived from the supplied rows; multiple questions
within one held-out episode are allowed. Duplicated observation IDs are rejected.
The calibration dataset must be declared and distinct even if no training dataset
is present. Source/task/time deduplication and truthful manifests remain the data
owner's responsibility: these checks catch declared overlap and mismatched bytes,
not undisclosed contamination, label independence, or genuine prior freezing.

Use `dataset_sha256(rows)` to compute the holdout digest. Canonicalization sorts
rows by `row_id`, serializes JSON with sorted object keys, separators `(',', ':')`,
`ensure_ascii=False`, and `allow_nan=False`, then hashes the UTF-8 bytes. This
excludes the surrounding provenance document to avoid self-reference. Array
order (including option order) and numeric spelling after JSON parsing remain
part of the canonical representation. Row and object-key order do not. A SHA-256
match proves integrity against the supplied manifest, not provenance authenticity.

Optional `error_costs` is a list of overrides, each with exactly `point_id`,
`question_id`, `true_label`, `predicted_label`, and `cost`. The directed label pair
must occur together in an observed closed menu at that point/question. Costs
must be finite, nonnegative numbers at most 1e12; duplicate overrides and nonzero
correct-prediction costs are rejected. The defaults are 0 for correct, 1 for
incorrect. A false-allow cost of 10 does not change the reverse-direction cost or
another point's loss. Costs are shared across option counts for that same scoped
label pair.

## Metrics and populations

The result has `schema_version: 1`, `purpose`, copied/canonicalized `provenance`,
`settings` (including the full cost specification), `overall`, and sorted `groups`.
Each group has `point_id`, `question_id`, `option_count`, and `metrics`. Grouping
by all three dimensions is mandatory; pooled overall metrics can conceal a weak
decision group and are not promotion gates.

Every metrics object reports:

- `row_count`, `scored_count`, `missing_distribution_count`
- `accuracy`: candidate top-1 accuracy among rows with a valid distribution
- `brier`: mean multiclass sum of squared probability errors over **all** closed
  classes, unnormalized by option count (range 0–2)
- `ece`: top-1 expected calibration error, weighted by scored count. Equal-width
  confidence bins are left-inclusive/right-exclusive, except the final bin
  includes 1. `calibration_bins` provides bounds, count, accuracy and mean
  confidence; empty bins have null accuracy/confidence
- `confidence_auroc`: AUROC of correctness (positive) versus candidate top-1
  confidence, with half credit for tied confidence. It includes positive and
  negative counts. `value` is null with `undefined_reason` of `no_predictions`,
  `all_correct`, or `all_incorrect` when the binary ranking is undefined
- `coverage_at_target_precision`: largest empirical acceptance coverage over
  observed confidence thresholds. Only non-fallback, non-unclear predictions
  are eligible; coverage divides by **all rows**, including missing predictions.
  Every tied-confidence block is accepted or rejected together. The object
  includes `target_precision`, `coverage`, `accepted_count`, `eligible_count`,
  measured `precision`, `threshold`, and `undefined_reason`. If no threshold meets
  the target, coverage/accepted count are zero and precision/threshold are null.
  Empty input has null coverage; absence of eligible predictions is explicit
- `fallback_count`, `fallback_rate`: actual fallback flag among all supplied rows
- `asymmetric_error_cost`: candidate top-1 `total`, `mean`, `scored_count`, and
  `basis: "candidate_top1_only"`. Valid candidate predictions still count when
  fallback was used; missing predictions are excluded. This does not measure the
  incumbent's outcome or assign an invented cost to an outage
- `unscored_metrics_reason`: `no_predictions` if none could be scored, else null.
  Accuracy/Brier/ECE/error-cost mean are null when undefined; no NaN is emitted

Coverage thresholds are **retrospective descriptive statistics**, not a fitted
policy, confidence bound, guaranteed precision, or enforcement qualification.
Do not tune on this holdout or feed its labels into training/calibration. Any
future rollout needs independently predeclared thresholds, domain labels,
adequate uncertainty/safety analysis, red-team evidence, live shadow comparison,
and explicit authorization. Changing question wording, options, model, or
calibration invalidates prior evidence for that bundle. This harness implements
none of that future promotion chain and makes no hardware/latency claims.
