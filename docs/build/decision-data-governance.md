# BE17: governed decision data and release tooling

## What is implemented

`python -m evals.decision_governance_cli` is an **offline local-file workflow**. Its consumers are dataset rebuild/export, scalar calibration refit, deletion-impact review, signed release verification, explicit operator bundle selection, and drift reports. It does not discover live receipts, train a candidate, call a teacher, download models, provision credentials, publish data, activate serving, or relax a Guard.

The checked-in `evals/decision_governance_fixtures.json` contains three explicitly synthetic governance examples, one per time partition. Their menu and question match BE15 `DP05:v1/scope`. These are plumbing fixtures, **not** the additional harness-recall training dataset and not evidence that recall works. The original intention-routing dataset and a trained checkpoint were not available in this checkout. Intention routing does not establish permission enforcement, tool selection, or calibrated harness recall.

Modules:

- `evals/decision_governance.py`: strict records, redaction, deterministic task/episode/time splits, frozen ledger, deletion lineage, receipt exports, offline calibration
- `evals/decision_governance_release.py`: Ed25519/public trust verification, pinned artifact bytes, independent-metric gates, complete bundle selection/rollback, compatibility projection, drift recommendations
- `evals/decision_governance_cli.py`: actual local CLI consumers with payload-free success/error diagnostics
- `tests/evals/test_decision_governance.py`: real CLI subprocesses and signed mock artifacts with ephemeral **test-only** keys

## Record and authority contract

A `DatasetRecord` contains `source_receipt`, `source_id`, `episode_id`, `task_id`, timezone-aware `observed_at`, `consent_purpose`, `scope`, `provenance`, `redacted_packet`, `independent_label`, `label_source`, `labeler`, `producer`, `contract_version`, `contract_digest`, `question_id`, and `deletion_state`.

`provenance` has `kind` (`synthetic`, `public`, or `private`), `license`, and `source_version`. Labels must come from a human, independent teacher, deterministic outcome, or explicitly synthetic fixture. Producer and labeler cannot be the same declared identity; unreviewed model outputs are denied. These fields are provenance attestations, not proof of a person's identity or a dataset's license. The operator must independently inspect and approve source rights and labeling evidence.

Packets have exactly `authority`, ordered `live_options`, and `state`. Seeded secrets and obvious credential patterns are removed from state recursively; secrets in keys or metadata are rejected, and redaction that would change authority or menu identities is rejected. Adversarial delimiters and menu order are preserved. Packets exceeding the configured UTF-8 byte ceiling are rejected, never truncated. The ceiling defaults to BE15's 16,384-byte envelope; it is not a measured token count. The proposed 320–768-token-style packet hypothesis, real tokenization, authority sufficiency, and serving limits still require measurement.

Purpose and scope must match each source's consent. Synthetic/public provenance is accepted locally without fetching anything. A `public` label does not itself establish redistribution rights. Private input requires an existing operator approval with exact source IDs, purpose, scope, resolved output destination, and whether packet inclusion is approved:

```json
{"approval_id":"approved-record","source_ids":["source-123"],"purpose":"routing_research","scope":"project-123","destination":"/approved/private/receipts.json","include_packets":false}
```

Do not invent approvals or write this registry to grant yourself access. A roadmap is not approval. Supplying an approval JSON is how an already-authorized operator records a bounded approval, not an authentication service. Default export omits all packets; `--include-packets` is explicit and requires the stronger private approval. Receipt hashes remain correlatable metadata, not guaranteed anonymization. Redaction is not a general sensitive-data detector.

## Deterministic synthetic rebuild

From the repository root, using the existing prepared interpreter:

```sh
mkdir -p /tmp/be17-example
cat > /tmp/be17-example/policy.json <<'JSON'
{"purpose":"governance_fixture","scope":"synthetic-project","train_before":"2026-02-01T00:00:00Z","calibration_before":"2026-03-01T00:00:00Z","packet_byte_limit":16384}
JSON
.venv/bin/python -m evals.decision_governance_cli build \
  --input evals/decision_governance_fixtures.json \
  --policy /tmp/be17-example/policy.json \
  --ledger /tmp/be17-example/frozen-ledger.json \
  --output /tmp/be17-example/receipts.json
```

Repeat the same command against the same ledger. Identical inputs produce identical exported bytes and manifest digest, regardless of input row order. The ledger is written atomically before the export; an interrupted output can be retried. Do not point an output or ledger at an input. Keep a single protected ledger for each governed dataset lineage; choosing a new ledger creates a new lineage, not permission to reuse an old holdout. Filesystem access and the approved trust/operator registries remain the operator's responsibility.

The chronological cutoffs define train, calibration, and frozen evaluation. Tasks, episodes, and equal redacted content cannot cross partitions, even under a different contract version. Exact duplicate content within a partition is collapsed while retaining every contributing source, episode, and task digest. Conflicting duplicate labels are rejected. Existing frozen membership and labels cannot change or grow; new holdout studies need a separately reviewed lineage. Reusing any previously frozen episode/task/content for training is rejected.

## Calibration refit boundary

`refit` accepts the receipt-only build and observations of the form:

```json
{"record_digest":"<calibration-record-sha256>","model_digest":"<pinned-checkpoint-sha256>","distribution":{"none":0.15,"project":0.7,"unclear":0.15}}
```

One observation is required for every calibration row, with the exact closed menu, independent label, one contract/question, and pinned candidate digest. Training and frozen evaluation rows cannot enter fitting. A predeclared temperature grid minimizes calibration negative log likelihood deterministically. This produces a versioned artifact with its semantic digest, source manifest, row IDs, contract digest/question, model digest, temperature, grid and calibration loss:

```sh
.venv/bin/python -m evals.decision_governance_cli refit \
  --input /approved/receipts.json --observations /approved/calibration-observations.json \
  --model-digest <checkpoint-sha256> --output /approved/offline-calibration.json
```

It is an **offline, unqualified scalar calibration artifact**, not a trained model, held-out result, installed calibrator, or deployment approval. BE15 `agent.decisions.calibration.evaluate` remains the descriptive frozen-holdout evaluator and does not fit this temperature. Independent held-out and red-team evaluation must follow fitting; no validation on calibration rows can certify a release.

## Deletion and invalidation

Pass a JSON list of deleted source IDs with `build --deleted-sources`. Deleted/withdrawn rows remove the entire source, and tombstone digests persist in the lineage ledger so later rebuilds cannot resurrect it. Frozen duplicates sharing a deleted contributor are conservatively removed together. The next export contains no rows from those sources.

`deletion-impact --input review-input.json --output deletion-review.json` consumes `{manifests, releases, deleted_sources}`. It lists invalidated dataset-manifest digests, checkpoints requiring review/retraining consideration, and an export/promotion hold. It never claims existing weights have unlearned anything. Preserve minimum compliant audit evidence; handle historical exported copies under the source's deletion/retention policy. Supply the resulting invalidated manifest digests to release checks. This workflow does not purge remote backups or already-distributed artifacts.

## Signed model-release workflow

`ModelReleaseManifest` pins checkpoint, dataset manifest, code and service digests, runtime versions, hardware description, seed, contract versions **and exact BE15 semantic digests**, calibration digest, every artifact's byte digest, metrics and uncertainty intervals, reproducibility mode/tolerances, safety limitations, approved signing identity, deployment scope, and complete rollback predecessor digest.

Artifacts are exactly `checkpoint`, `dataset`, `code`, `service`, `calibration`, `contracts`, and `evidence`. Paths are relative to one artifact root; escaping paths, unavailable files, and oversized artifacts are rejected. Checkpoints are hashed as opaque bytes and never deserialized/executed. Dataset, contract, calibration, model and metric bindings must agree. The calibration digest is its canonical artifact body digest excluding its own digest field; the artifact byte digest separately pins exact JSON bytes.

The signed envelope is `{manifest, signature_hex}`; the Ed25519 signature covers canonical JSON manifest bytes (`sort_keys=True`, compact separators, ASCII escaping, finite values). The approved public trust registry maps signing identity to `{public_key_hex, approved_scopes, revoked}`. The CLI only reads existing public keys and verifies signatures; it never generates or installs a signing identity. Keys exist only ephemerally in unit tests. Do not put private keys in manifests or input files.

Release command input has `target: {envelope, artifact_root, paths}`, optional `current` of the same shape, an independent `gate`, `invalidated_datasets`, and `revoked` release digests. The gate specifies approved independent evaluators, minimum sample count, required red-team pass, and each metric's direction, absolute threshold, and permitted regression. Conservative uncertainty bounds must pass. Evidence sample count must match the frozen population; candidate/incumbent checkpoint identities and source-holdout digest are bound. Signing establishes integrity/provenance, not honesty of observations or safety. Independently controlled evaluator/approval records remain necessary.

```sh
.venv/bin/python -m evals.decision_governance_cli release --action verify \
  --input /approved/release-check.json --trust /approved/public-signers.json \
  --output /approved/verification.json
.venv/bin/python -m evals.decision_governance_cli release --action runtime-projection \
  --input /approved/release-check.json --trust /approved/public-signers.json \
  --output /approved/runtime-projection.json
```

`runtime-projection` checks installed BE15 `contract_for(point, version)` and exact semantic digests/questions, then constructs the existing `ModelBundle(model_digest, calibration_digest, service_digest)` unchanged. Unknown versions or changed contracts fail. This is envelope compatibility only: it does not load weights, install a temperature transform, change the BE15 shadow/disabled configuration, or activate serving.

`compare` returns independent candidate/incumbent metrics and gate outcome. `reproduce` compares reference/target releases under pinned dataset/code/service/runtime/hardware/seed/contracts and predeclared exact-artifact or statistical metric tolerance. This is a reproduction check, not a safety certificate.

`shadow`, `promote`, and `rollback` additionally require `--operator` and an existing independent `--operators` registry. Candidate producers cannot approve themselves. Production promotion rejects synthetic data/evidence/calibration and shadow-only releases. Targets invalidated by deletion or revoked releases are denied. Rollback checks the exact predecessor and writes its **entire model + service + dataset + contract + calibration bundle** to a local selection receipt. An invalidated current dataset can be left for a separately valid predecessor. All selections explicitly report `serving_activated: false`; installing a selected bundle into a real serving runtime remains a separate authorized integration gate.

## Drift and controlled next steps

`drift` consumes family (`router`/`guard`), observed and limit maps for `outcome_error`, `fallback_rate`, `override_rate`, `confidence_shift`, and `teacher_disagreement`, plus `predecessor_available`. Breaches hold promotion; Router drift proposes controlled retraining review, Guard drift requests investigation and renewed red-team gates. A rollback may be recommended. No automatic training, rollback, or privilege relaxation occurs.

## Deferred experiment/runbook gates

1. Locate the actual intention dataset and exact RLCD source/commit/recipe. None was supplied here; do not invent results or fetch/train a substitute.
2. Inspect intention coverage, source licenses, redistribution restrictions, consent/purpose, independent labels, and missing DP05/DP16 cases. Intention labels alone do not solve recall, tool selection, permissions, or calibration.
3. After backend and real memory integration are complete, separately design the requested additional harness dataset: no recall/project/personal/allowed combinations, forbidden specialist personal access, unclear/defer, stale/conflicting records, unavailable memory service, denied scope, correction, and invalidation. Use only authorized synthetic/redacted inputs and separate task/time train/calibration/frozen episodes.
4. Validate the exact RLCD license, dependency/toolchain pins, model license, hardware precision, architecture and tokenizer compatibility. Investigate Apple silicon versus an explicitly approved private GPU only with the actual artifacts. Four epochs/group four/sigma/LR settings are hypotheses, not verified compatibility or optimal settings.
5. Obtain explicit approval for the precise data, remote teacher/GPU destination and costs before transmission. This code does not configure credentials or create ongoing access.
6. Run the authorized experiment, preserve dataset/code/service/runtime/hardware/seeds, measure serving packet limits, refit calibration, and evaluate independent immutable holdouts and red-team cases. Freeze statistical tolerances **before** evaluating a candidate.
7. Use an existing approved signing identity, verify every artifact, deploy first in a separately authorized shadow integration, compare incumbent/candidate, and obtain operator promotion approval. Test complete predecessor rollback in that runtime.

Current acceptance is limited to the verified local governance/CLI behavior and signed synthetic fixtures recorded in `be17-validation.json`. Actual training reproducibility, production holdouts, RLCD/toolchain compatibility, installed calibration and live serving promotion/rollback remain deferred gates, not successful experiments.
