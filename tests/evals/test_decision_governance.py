"""Local BE17 governance contracts. All records, checkpoints and keys are synthetic."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent.decisions.registry import contract_for

from evals.decision_governance import (
    DatasetPolicy, GovernanceError, build_dataset, canonical, deletion_impact, digest,
    load_build, refit_calibration,
)
from evals.decision_governance_release import (
    ModelReleaseManifest, assess_drift, check_reproducibility, compare_shadow, decision_bundle,
    operator_transition, verify_release,
)


POLICY = DatasetPolicy("routing_research", "fixture-project", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z")
ROOT = Path(__file__).resolve().parents[2]
SECRET = "seeded-fixture-secret-123456"


def record(index, month, **updates):
    result = {"source_receipt": f"receipt-{index}", "source_id": f"source-{index}",
              "episode_id": f"episode-{index}", "task_id": f"task-{index}",
              "observed_at": f"2026-{month:02d}-02T00:00:00Z",
              "consent_purpose": ["routing_research"], "scope": "fixture-project",
              "provenance": {"kind": "synthetic", "license": "fixture-only", "source_version": "v1"},
              "redacted_packet": {"authority": {"personal_recall_allowed": False, "scope": "fixture-project"},
                                  "live_options": ["none", "project", "unclear"],
                                  "state": {"fixture_id": index, "text": "<untrusted>" + SECRET + "</untrusted>"}},
              "independent_label": "project", "label_source": "synthetic_fixture",
              "labeler": "fixture-author", "producer": "candidate", "contract_version": "DP05:v1",
              "contract_digest": contract_for("DP05", 1).contract_digest, "question_id": "scope",
              "deletion_state": "active"}
    result.update(updates)
    return result


def records():
    return [record("train", 1), record("calibration", 2), record("holdout", 3)]


def build(tmp_path, rows=None, **kwargs):
    return build_dataset(rows or records(), POLICY, destination=tmp_path / "export.json",
                         seeded_secrets=(SECRET,), **kwargs)


def observations(dataset, model_digest):
    return [{"record_digest": row["record_digest"], "model_digest": model_digest,
             "distribution": {"none": .15, "project": .7, "unclear": .15}}
            for row in dataset.records if row["split"] == "calibration"]


def write(path, value):
    path.write_bytes(canonical(value))
    return str(path)


def test_rebuild_redacts_without_reordering_or_losing_authority(tmp_path):
    first = build(tmp_path, include_packets=True)
    second = build(tmp_path, list(reversed(records())), include_packets=True, previous_manifest=first.manifest)
    assert canonical(first.export(include_packets=True)) == canonical(second.export(include_packets=True))
    assert SECRET.encode() not in canonical(first.export(include_packets=True))
    for row in first.records:
        assert row["redacted_packet"]["live_options"] == ["none", "project", "unclear"]
        assert row["redacted_packet"]["authority"]["personal_recall_allowed"] is False
        assert row["redacted_packet"]["state"]["text"] == "<untrusted>[REDACTED]</untrusted>"
    assert all("redacted_packet" not in row for row in first.export()["records"])
    assert load_build(first.export()).manifest == first.manifest


@pytest.mark.parametrize("change,code", [
    ({"label_source": "laya_output"}, "unreviewed_self_label_denied"),
    ({"labeler": "candidate"}, "self_label_denied"),
    ({"scope": "personal"}, "consent_scope_mismatch"),
    ({"consent_purpose": ["different_research"]}, "consent_scope_mismatch"),
])
def test_independent_labels_consent_and_scope_are_required(tmp_path, change, code):
    with pytest.raises(GovernanceError, match=code):
        build(tmp_path, [record("bad", 1, **change)])


def test_private_inputs_need_destination_and_payload_specific_approval(tmp_path):
    rows = records()
    rows[0]["provenance"]["kind"] = "private"
    rows[0]["label_source"] = "human"
    with pytest.raises(GovernanceError, match="bounded_approval"):
        build(tmp_path, rows)
    approval = {"approval_id": "synthetic-test-approval", "source_ids": ["source-train"],
                "purpose": POLICY.purpose, "scope": POLICY.scope,
                "destination": str(tmp_path / "export.json"), "include_packets": False}
    assert build(tmp_path, rows, approvals=[approval]).records
    with pytest.raises(GovernanceError, match="bounded_approval"):
        build(tmp_path, rows, approvals=[approval], include_packets=True)
    approval["include_packets"] = True
    approval["destination"] = str(tmp_path / "wrong.json")
    with pytest.raises(GovernanceError, match="bounded_approval"):
        build(tmp_path, rows, approvals=[approval], include_packets=True)
    approval["destination"] = str(tmp_path / "export.json")
    assert build(tmp_path, rows, approvals=[approval], include_packets=True).export(include_packets=True)


@pytest.mark.parametrize("key", ["episode_id", "task_id", "redacted_packet"])
def test_time_split_rejects_episode_task_and_content_overlap(tmp_path, key):
    rows = records()
    rows[1][key] = deepcopy(rows[0][key])
    with pytest.raises(GovernanceError, match="split_leakage_denied"):
        build(tmp_path, rows)


def test_dedupe_retains_all_source_lineage_and_rejects_conflicting_labels(tmp_path):
    first = record("a", 1)
    duplicate = record("b", 1, redacted_packet=deepcopy(first["redacted_packet"]))
    result = build(tmp_path, [first, duplicate])
    assert len(result.records) == 1
    assert result.records[0]["source_digests"] == sorted([digest("source-a"), digest("source-b")])
    duplicate["independent_label"] = "none"
    with pytest.raises(GovernanceError, match="conflicting_duplicate_label"):
        build(tmp_path, [first, duplicate])


def test_holdout_is_immutable_and_deleted_sources_never_reappear(tmp_path):
    original = build(tmp_path)
    changed = records()
    changed[-1]["independent_label"] = "none"
    with pytest.raises(GovernanceError, match="frozen_holdout_contamination"):
        build(tmp_path, changed, previous_manifest=original.manifest)
    with pytest.raises(GovernanceError, match="frozen_holdout_contamination"):
        build(tmp_path, records() + [record("new-eval", 4)], previous_manifest=original.manifest)
    deleted = build(tmp_path, deleted_sources=("source-holdout",), previous_manifest=original.manifest)
    assert deleted.manifest["splits"]["frozen_eval"] == []
    assert digest("source-holdout") not in deleted.manifest["source_digests"]
    rebuilt = build(tmp_path, previous_manifest=deleted.manifest)
    assert rebuilt.manifest == deleted.manifest
    checkpoint = hashlib.sha256(b"synthetic-checkpoint").hexdigest()
    impact = deletion_impact([original.manifest], [{"checkpoint_digest": checkpoint,
                              "dataset_manifest": original.manifest["manifest_digest"]}], ["source-holdout"])
    assert impact["invalidated_dataset_manifests"] == [original.manifest["manifest_digest"]]
    assert impact["checkpoint_review_required"] == [checkpoint]
    assert impact["unlearning_claim"] is False


def test_source_withdrawal_removes_all_its_rows_and_duplicate_holdout_lineage(tmp_path):
    first = record("a", 3)
    duplicate = record("b", 3, redacted_packet=deepcopy(first["redacted_packet"]))
    initial = build(tmp_path, [first, duplicate])
    first["deletion_state"] = "withdrawn"
    rebuilt = build(tmp_path, [first, duplicate], previous_manifest=initial.manifest)
    assert not rebuilt.records
    assert digest("source-a") in rebuilt.manifest["deleted_sources"]
    assert digest("source-b") in rebuilt.manifest["deleted_sources"]


def test_packet_limit_never_truncates_and_secrets_cannot_change_authority(tmp_path):
    rows = records()
    rows[0]["redacted_packet"]["state"]["text"] = "x" * 20000
    with pytest.raises(GovernanceError, match="packet_too_large_no_truncation"):
        build(tmp_path, rows)
    rows = records()
    rows[0]["redacted_packet"]["authority"]["secret"] = SECRET
    with pytest.raises(GovernanceError, match="redaction_would_change_authority"):
        build(tmp_path, rows)


def test_refit_uses_only_calibration_receipts_and_pins_artifact(tmp_path):
    dataset = load_build(build(tmp_path).export())
    model = digest("mock-checkpoint")
    obs = observations(dataset, model)
    fitted = refit_calibration(dataset, obs, model_digest=model)
    assert fitted == refit_calibration(dataset, list(reversed(obs)), model_digest=model)
    assert fitted["record_digests"] == sorted(dataset.manifest["splits"]["calibration"])
    assert not fitted["trained_model"] and not fitted["production_qualified"]
    obs[0]["record_digest"] = dataset.manifest["splits"]["frozen_eval"][0]
    with pytest.raises(GovernanceError, match="calibration_split_leakage"):
        refit_calibration(dataset, obs, model_digest=model)
    altered = build(tmp_path).export()
    altered["records"][0]["independent_label"] = "none"
    with pytest.raises(GovernanceError, match="build_record_tampered"):
        load_build(altered)


def mock_release(tmp_path, *, predecessor=None, release_id="mock-v1", contracts=None, production=False, dataset_rows=None):
    tmp_path.mkdir(exist_ok=True)
    contracts = contracts or {"DP05": {"version": 1, "digest": contract_for("DP05", 1).contract_digest}}
    dataset_rows = dataset_rows or records()
    for row in dataset_rows:
        row["contract_version"] = "DP05:v" + str(contracts["DP05"]["version"])
        row["contract_digest"] = contracts["DP05"]["digest"]
    dataset = build(tmp_path, dataset_rows)
    checkpoint = ("opaque synthetic fixture checkpoint " + release_id).encode()
    checkpoint_digest = hashlib.sha256(checkpoint).hexdigest()
    calibration = refit_calibration(dataset, observations(dataset, checkpoint_digest), model_digest=checkpoint_digest)
    metrics = {"accuracy": .97, "unsafe_rate": 0.}
    uncertainty = {"accuracy": [.95, .99], "unsafe_rate": [0., .01]}
    evidence = {"candidate_checkpoint": checkpoint_digest,
                "incumbent_checkpoint": predecessor.manifest.checkpoint_digest if predecessor else digest("mock-incumbent"),
                "dataset_manifest": dataset.manifest["manifest_digest"],
                "holdout_digest": digest(dataset.manifest["frozen_holdout"]),
                "evaluator": "independent-fixture-reviewer", "producer": "candidate", "synthetic": True,
                "frozen": True, "sample_count": len(dataset.manifest["splits"]["frozen_eval"]), "metrics": metrics,
                "incumbent_metrics": {"accuracy": .95, "unsafe_rate": 0.}, "uncertainty": uncertainty,
                "red_team_pass": True}
    blobs = {"checkpoint": checkpoint, "dataset": canonical(dataset.manifest), "code": b"mock-code-fixture-v1", "service": b"mock-service-fixture-v1",
             "calibration": canonical(calibration), "contracts": canonical(contracts), "evidence": canonical(evidence)}
    paths = {name: name + ".fixture" for name in blobs}
    for name, blob in blobs.items():
        (tmp_path / paths[name]).write_bytes(blob)
    artifacts = {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()}
    manifest = ModelReleaseManifest(release_id=release_id, checkpoint_digest=checkpoint_digest,
        dataset_manifest=dataset.manifest["manifest_digest"], code_digest=artifacts["code"], service_digest=artifacts["service"],
        runtime={"python": "3.14-fixture"}, hardware={"device": "mock-cpu"}, seed=17,
        contract_versions=contracts, calibration_digest=calibration["calibration_digest"], artifact_digests=artifacts,
        metrics=metrics, uncertainty=uncertainty, reproducibility={"mode": "exact_digest",
        "metric_tolerances": {"accuracy": 0., "unsafe_rate": 0.}}, safety_limitations=["synthetic_only_no_safety_claim"],
        signing_identity="temporary-test-" + release_id, predecessor=predecessor.manifest.release_digest if predecessor else None,
        deployment_scope="production" if production else "shadow")
    key = Ed25519PrivateKey.generate()  # Ephemeral test-only key; never written or used as a live credential.
    raw = asdict(manifest)
    envelope = {"manifest": raw, "signature_hex": key.sign(canonical(raw)).hex()}
    trust = {manifest.signing_identity: {"public_key_hex": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
                                       "approved_scopes": ["shadow", "production"], "revoked": False}}
    return envelope, trust, paths, key


def verify_fixture(root, fixture, **kwargs):
    envelope, trust, paths, _ = fixture
    return verify_release(envelope, trust=trust, artifact_root=root, paths=paths, **kwargs)


def gate():
    return {"approved_evaluators": ["independent-fixture-reviewer"], "minimum_samples": 1,
            "require_red_team": True, "metrics": {
                "accuracy": {"direction": "higher", "threshold": .94, "max_regression": .01},
                "unsafe_rate": {"direction": "lower", "threshold": .01, "max_regression": 0.}}}


def test_signed_mock_release_integrity_and_shadow_gate(tmp_path):
    fixture = mock_release(tmp_path)
    verified = verify_fixture(tmp_path, fixture)
    assert compare_shadow(verified, gate())["passed"]
    assert check_reproducibility(verified, verified)["within_declared_tolerance"]
    modified = deepcopy(fixture[0])
    modified["manifest"]["metrics"]["accuracy"] = .98
    with pytest.raises(GovernanceError, match="release_signature_mismatch"):
        verify_release(modified, trust=fixture[1], artifact_root=tmp_path, paths=fixture[2])
    (tmp_path / fixture[2]["checkpoint"]).write_bytes(b"malicious replacement; never deserialized")
    with pytest.raises(GovernanceError, match="artifact_digest_mismatch"):
        verify_fixture(tmp_path, fixture)


def test_signing_requires_approved_nonrevoked_identity_and_contained_artifacts(tmp_path):
    fixture = mock_release(tmp_path)
    with pytest.raises(GovernanceError, match="unapproved_signing_identity"):
        verify_release(fixture[0], trust={}, artifact_root=tmp_path, paths=fixture[2])
    fixture[1][fixture[0]["manifest"]["signing_identity"]]["revoked"] = True
    with pytest.raises(GovernanceError, match="signing_identity_not_approved"):
        verify_fixture(tmp_path, fixture)
    fixture[1][fixture[0]["manifest"]["signing_identity"]]["revoked"] = False
    fixture[2]["checkpoint"] = "../escape.fixture"
    with pytest.raises(GovernanceError, match="artifact_path_escape"):
        verify_fixture(tmp_path, fixture)


def test_synthetic_or_self_evaluation_cannot_qualify_production(tmp_path):
    fixture = mock_release(tmp_path, production=True)
    with pytest.raises(GovernanceError, match="synthetic_cannot_certify_production"):
        verify_fixture(tmp_path, fixture)
    fixture = mock_release(tmp_path)
    evidence_path = tmp_path / fixture[2]["evidence"]
    evidence = json.loads(evidence_path.read_bytes())
    evidence["evaluator"] = evidence["producer"]
    evidence_path.write_bytes(canonical(evidence))
    fixture[0]["manifest"]["artifact_digests"]["evidence"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    fixture[0]["signature_hex"] = fixture[3].sign(canonical(fixture[0]["manifest"])).hex()
    with pytest.raises(GovernanceError, match="self_evaluation_denied"):
        verify_fixture(tmp_path, fixture)


def test_operator_rollback_restores_complete_predecessor_contract_and_calibration(tmp_path):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    old = verify_fixture(old_root, mock_release(old_root, contracts={"DP05": {"version": 1, "digest": contract_for("DP05", 1).contract_digest}}))
    new = verify_fixture(new_root, mock_release(new_root, predecessor=old, release_id="mock-v2", contracts={"DP05": {"version": 2, "digest": digest("hypothetical-DP05-v2")}}))
    approval = {"operator": "fixture-operator", "approved_operators": ["fixture-operator"], "gate": gate()}
    selected = operator_transition(action="shadow", current=old, target=new, **approval)
    restored = operator_transition(action="rollback", current=new, target=old, **approval)
    assert selected["selected_bundle"]["contract_versions"] == {"DP05": {"version": 2, "digest": digest("hypothetical-DP05-v2")}}
    assert restored["selected_bundle"] == asdict(old.manifest)
    assert not restored["serving_activated"]
    with pytest.raises(GovernanceError, match="operator_approval_required"):
        operator_transition(action="shadow", current=old, target=new, operator="candidate", approved_operators=["candidate"], gate=gate())
    with pytest.raises(GovernanceError, match="shadow_only_release"):
        operator_transition(action="promote", current=old, target=new, **approval)
    with pytest.raises(GovernanceError, match="revoked_or_invalidated_release"):
        operator_transition(action="rollback", current=new, target=old,
                            invalidated_datasets=(old.manifest.dataset_manifest,), **approval)
    denied = gate()
    denied["metrics"]["accuracy"]["threshold"] = .99
    with pytest.raises(GovernanceError, match="independent_promotion_gate_failed"):
        operator_transition(action="shadow", current=old, target=new, **{**approval, "gate": denied})


def test_guard_and_router_drift_only_recommend_explicit_review():
    observed = {"outcome_error": .1, "fallback_rate": .2, "override_rate": .2,
                "confidence_shift": .3, "teacher_disagreement": .3}
    limits = dict.fromkeys(observed, .1)
    for family, recommendation in (("guard", "investigate_and_repeat_red_team"),
                                    ("router", "controlled_retraining_proposal")):
        result = assess_drift(family=family, observed=observed, limits=limits, predecessor_available=True)
        assert result["recommendation"] == recommendation
        assert result["hold_promotion"] and result["rollback_recommended"]
        assert not result["automatic_training"] and not result["privilege_relaxation"]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "evals.decision_governance_cli", *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, timeout=15)


def test_real_cli_deterministic_rebuild_refit_and_deletion(tmp_path):
    source = write(tmp_path / "records.json", records())
    policy = write(tmp_path / "policy.json", asdict(POLICY))
    seeds = write(tmp_path / "seeds.json", [SECRET])
    output, ledger = tmp_path / "export.json", tmp_path / "ledger.json"
    args = ["build", "--input", source, "--policy", policy, "--secret-seeds", seeds,
            "--output", output, "--ledger", ledger]
    first = cli(*args)
    assert first.returncode == 0, first.stderr
    first_bytes = output.read_bytes()
    second = cli(*args)
    assert second.returncode == 0 and first_bytes == output.read_bytes()
    assert SECRET not in first.stdout + second.stdout + output.read_text()
    dataset = load_build(json.loads(output.read_bytes()))
    obs = write(tmp_path / "observations.json", observations(dataset, digest("cli-mock")))
    calibrated = cli("refit", "--input", output, "--observations", obs,
                     "--model-digest", digest("cli-mock"), "--output", tmp_path / "calibration.json")
    assert calibrated.returncode == 0, calibrated.stderr
    removed = write(tmp_path / "deleted.json", ["source-train"])
    rebuilt = cli(*args, "--deleted-sources", removed)
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert digest("source-train") not in json.loads(output.read_bytes())["manifest"]["source_digests"]


def test_real_cli_verifies_signed_fixture_and_emits_safe_errors(tmp_path):
    fixture = mock_release(tmp_path)
    envelope_path = write(tmp_path / "envelope.json", fixture[0])
    trust_path = write(tmp_path / "trust.json", fixture[1])
    config_path = write(tmp_path / "config.json", {"target": {"envelope": envelope_path,
                        "artifact_root": str(tmp_path), "paths": fixture[2]}})
    result = cli("release", "--action", "verify", "--input", config_path, "--trust", trust_path,
                 "--output", tmp_path / "verification.json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"]
    projected = cli("release", "--action", "runtime-projection", "--input", config_path, "--trust", trust_path,
                    "--output", tmp_path / "projection.json")
    assert projected.returncode == 0, projected.stderr
    assert json.loads(projected.stdout)["bundle"]["service_digest"] == fixture[0]["manifest"]["service_digest"]
    assert not json.loads(projected.stdout)["calibrator_installed"]
    (tmp_path / fixture[2]["checkpoint"]).write_text(SECRET)
    failure = cli("release", "--action", "verify", "--input", config_path, "--trust", trust_path,
                  "--output", tmp_path / "failed.json")
    assert failure.returncode == 2 and "artifact_digest_mismatch" in failure.stderr
    assert SECRET not in failure.stderr + failure.stdout
    assert not (tmp_path / "failed.json").exists()


def test_frozen_duplicate_episode_lineage_cannot_reenter_training(tmp_path):
    a, b = record("a", 3), record("b", 3)
    b["redacted_packet"] = deepcopy(a["redacted_packet"])
    first = build(tmp_path, [a, b])
    assert set(first.manifest["frozen_holdout"][0]["episode_digests"]) == {digest("episode-a"), digest("episode-b")}
    train = record("new", 1, episode_id="episode-b")
    with pytest.raises(GovernanceError, match="split_leakage|frozen_holdout_reuse"):
        build(tmp_path, [a, b, train], previous_manifest=first.manifest)


def test_cross_contract_duplicates_cannot_cross_splits(tmp_path):
    rows = records()
    rows[1]["redacted_packet"] = deepcopy(rows[0]["redacted_packet"])
    rows[1]["contract_version"] = "DP05:v2"
    with pytest.raises(GovernanceError, match="split_leakage"):
        build(tmp_path, rows)


def test_cli_rollback_can_leave_invalidated_current_for_unaffected_predecessor(tmp_path):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    old_fixture = mock_release(old_root)
    old = verify_fixture(old_root, old_fixture)
    revised = records()
    revised[0]["redacted_packet"]["state"]["new_synthetic_fixture"] = True
    new_fixture = mock_release(new_root, predecessor=old, release_id="mock-v2", dataset_rows=revised)
    new = verify_fixture(new_root, new_fixture)
    target = {"envelope": write(old_root / "envelope.json", old_fixture[0]),
              "artifact_root": str(old_root), "paths": old_fixture[2]}
    current = {"envelope": write(new_root / "envelope.json", new_fixture[0]),
               "artifact_root": str(new_root), "paths": new_fixture[2]}
    config = write(tmp_path / "rollback.json", {"target": target, "current": current, "gate": gate(),
                   "invalidated_datasets": [new.manifest.dataset_manifest]})
    trust = write(tmp_path / "trust.json", {**old_fixture[1], **new_fixture[1]})
    operators = write(tmp_path / "operators.json", ["fixture-operator"])
    destination = tmp_path / "selection.json"
    result = cli("release", "--action", "rollback", "--input", config, "--trust", trust,
                 "--operator", "fixture-operator", "--operators", operators, "--output", destination)
    assert result.returncode == 0, result.stderr
    assert json.loads(destination.read_bytes())["selected_bundle"] == asdict(old.manifest)


def test_release_rejects_inflated_independent_sample_population(tmp_path):
    fixture = mock_release(tmp_path)
    path = tmp_path / fixture[2]["evidence"]
    evidence = json.loads(path.read_bytes())
    evidence["sample_count"] += 1000
    path.write_bytes(canonical(evidence))
    fixture[0]["manifest"]["artifact_digests"]["evidence"] = hashlib.sha256(path.read_bytes()).hexdigest()
    fixture[0]["signature_hex"] = fixture[3].sign(canonical(fixture[0]["manifest"])).hex()
    with pytest.raises(GovernanceError, match="missing_independent_holdout"):
        verify_fixture(tmp_path, fixture)


def test_verified_release_projects_exact_be15_bundle_without_installing(tmp_path):
    fixture = mock_release(tmp_path)
    verified = verify_fixture(tmp_path, fixture)
    bundle = decision_bundle(verified)
    assert records()[0]["redacted_packet"]["live_options"] == list(contract_for("DP05", 1).question("scope").options)
    assert bundle.model_digest == verified.manifest.checkpoint_digest
    assert bundle.calibration_digest == verified.manifest.calibration_digest
    assert bundle.service_digest == verified.manifest.service_digest
    assert verified.manifest.contract_versions["DP05"]["digest"] == contract_for("DP05", 1).contract_digest
    future_root = tmp_path / "future"
    future = verify_fixture(future_root, mock_release(future_root,
        contracts={"DP05": {"version": 2, "digest": digest("unsupported-contract")}}))
    with pytest.raises(GovernanceError, match="runtime_contract_unavailable"):
        decision_bundle(future)


def test_bundled_synthetic_fixture_matches_live_be15_question_contract(tmp_path):
    rows = json.loads((ROOT / "evals" / "decision_governance_fixtures.json").read_bytes())
    policy = DatasetPolicy("governance_fixture", "synthetic-project", POLICY.train_before, POLICY.calibration_before)
    result = build_dataset(rows, policy, destination=tmp_path / "receipt.json")
    for row in result.records:
        point, version = row["contract_version"].split(":v")
        contract = contract_for(point, int(version))
        assert row["contract_digest"] == contract.contract_digest
        assert row["live_options"] == list(contract.question(row["question_id"]).options)
    assert all(result.manifest["splits"].values())
    assert result.manifest["synthetic"]


def test_verified_snapshot_cannot_be_mutated_after_signature_check(tmp_path):
    verified = verify_fixture(tmp_path, mock_release(tmp_path))
    verified.evidence["red_team_pass"] = False
    with pytest.raises(GovernanceError, match="verified_snapshot_mutated"):
        compare_shadow(verified, gate())


def test_separate_signed_mock_rebuild_matches_exact_artifact_tolerance(tmp_path):
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first = verify_fixture(first_root, mock_release(first_root))
    second = verify_fixture(second_root, mock_release(second_root))
    assert check_reproducibility(first, second)["within_declared_tolerance"]


def test_seeded_metadata_secrets_are_rejected_and_pattern_credentials_are_redacted(tmp_path):
    rows = records()
    rows[0]["provenance"]["source_version"] = SECRET
    with pytest.raises(GovernanceError, match="secret_in_metadata"):
        build(tmp_path, rows)
    rows = records()
    rows[0]["redacted_packet"]["state"]["text"] = "password=synthetic-password-value"
    output = build(tmp_path, rows, include_packets=True).export(include_packets=True)
    assert b"synthetic-password-value" not in canonical(output)
