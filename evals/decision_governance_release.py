"""Verify pinned release bundles and record operator-controlled local transitions.

Ed25519 keys are read-only public trust anchors provisioned outside this module.
No key creation, checkpoint deserialization, serving activation or retraining.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from evals.decision_governance import (
    GovernanceError, canonical, digest, fields, finite, identifier, require,
    sha256, verify_dataset_manifest,
)


ARTIFACT_NAMES = {"checkpoint", "dataset", "code", "service", "calibration", "contracts", "evidence"}


@dataclass(frozen=True)
class ModelReleaseManifest:
    release_id: str
    checkpoint_digest: str
    dataset_manifest: str
    code_digest: str
    service_digest: str
    runtime: dict
    hardware: dict
    seed: int
    contract_versions: dict
    calibration_digest: str
    artifact_digests: dict
    metrics: dict
    uncertainty: dict
    reproducibility: dict
    safety_limitations: list[str]
    signing_identity: str
    predecessor: str | None
    deployment_scope: str
    schema_version: int = 1

    @classmethod
    def parse(cls, raw: dict) -> ModelReleaseManifest:
        fields(raw, set(cls.__dataclass_fields__), "invalid_release_fields")
        require(raw["schema_version"] == 1, "invalid_release_version")
        for key in ("release_id", "signing_identity"):
            identifier(raw[key])
        for key in ("checkpoint_digest", "dataset_manifest", "code_digest", "service_digest", "calibration_digest"):
            sha256(raw[key])
        if raw["predecessor"] is not None:
            sha256(raw["predecessor"])
        require(raw["deployment_scope"] in ("shadow", "production"), "invalid_deployment_scope")
        require(type(raw["seed"]) is int and 0 <= raw["seed"] < 2**64, "invalid_seed")
        for section in ("runtime", "hardware"):
            require(isinstance(raw[section], dict) and raw[section], "missing_release_pins")
            for key, value in raw[section].items():
                identifier(key)
                identifier(value)
        require(isinstance(raw["contract_versions"], dict) and raw["contract_versions"], "missing_contract_pins")
        for point, pin in raw["contract_versions"].items():
            identifier(point)
            fields(pin, {"version", "digest"}, "invalid_contract_pin")
            require(type(pin["version"]) is int and pin["version"] > 0, "invalid_contract_pin")
            sha256(pin["digest"])
        fields(raw["artifact_digests"], ARTIFACT_NAMES, "invalid_artifact_set")
        for value in raw["artifact_digests"].values():
            sha256(value)
        require(raw["checkpoint_digest"] == raw["artifact_digests"]["checkpoint"]
                and raw["code_digest"] == raw["artifact_digests"]["code"]
                and raw["service_digest"] == raw["artifact_digests"]["service"], "release_pin_mismatch")
        require(isinstance(raw["metrics"], dict) and raw["metrics"], "missing_metrics")
        for key, value in raw["metrics"].items():
            identifier(key)
            finite(value, 0, 1e12)
        fields(raw["uncertainty"], set(raw["metrics"]), "missing_uncertainty")
        for key, bounds in raw["uncertainty"].items():
            require(isinstance(bounds, list) and len(bounds) == 2, "invalid_uncertainty")
            lower, upper = (finite(value, 0, 1e12) for value in bounds)
            require(lower <= raw["metrics"][key] <= upper, "invalid_uncertainty")
        fields(raw["reproducibility"], {"mode", "metric_tolerances"}, "invalid_reproducibility")
        require(raw["reproducibility"]["mode"] in ("exact_digest", "statistical"), "invalid_reproducibility")
        tolerances = raw["reproducibility"]["metric_tolerances"]
        fields(tolerances, set(raw["metrics"]), "missing_reproducibility_tolerances")
        for value in tolerances.values():
            finite(value, 0, 1e12)
        require(isinstance(raw["safety_limitations"], list) and raw["safety_limitations"], "missing_safety_limitations")
        for limitation in raw["safety_limitations"]:
            identifier(limitation)
        return cls(**json.loads(canonical(raw)))

    @property
    def release_digest(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class VerifiedRelease:
    """Verified snapshot; the CLI verifies signed files immediately before selection."""
    manifest: ModelReleaseManifest
    dataset: dict
    evidence: dict
    verified_manifest_digest: str
    verified_dataset_digest: str
    verified_evidence_digest: str


def _artifact_bytes(root: Path, paths: dict, expected: dict) -> dict:
    fields(paths, ARTIFACT_NAMES, "invalid_artifact_paths")
    root = root.resolve()
    blobs = {}
    for name in sorted(ARTIFACT_NAMES):
        require(isinstance(paths[name], str), "invalid_artifact_path")
        relative = Path(paths[name])
        require(not relative.is_absolute() and ".." not in relative.parts, "artifact_path_escape")
        target = root / relative
        require(target.resolve().is_relative_to(root), "artifact_path_escape")
        require(not target.is_symlink() and target.is_file(), "artifact_unavailable")
        # Read opaque checkpoint bytes only; never pickle/torch.load supplied files.
        require(target.stat().st_size <= 256 * 1024 * 1024, "artifact_size_limit")
        blob = target.read_bytes()
        require(hashlib.sha256(blob).hexdigest() == expected[name], "artifact_digest_mismatch")
        blobs[name] = blob
    return blobs


def verify_release(envelope: dict, *, trust: dict, artifact_root: Path, paths: dict,
                   invalidated_datasets: tuple[str, ...] = ()) -> VerifiedRelease:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    fields(envelope, {"manifest", "signature_hex"}, "invalid_signed_envelope")
    manifest = ModelReleaseManifest.parse(envelope["manifest"])
    require(manifest.dataset_manifest not in invalidated_datasets, "dataset_invalidated")
    require(manifest.signing_identity in trust, "unapproved_signing_identity")
    anchor = trust[manifest.signing_identity]
    fields(anchor, {"public_key_hex", "approved_scopes", "revoked"}, "invalid_trust_anchor")
    require(anchor["revoked"] is False and isinstance(anchor["approved_scopes"], list)
            and manifest.deployment_scope in anchor["approved_scopes"], "signing_identity_not_approved")
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(anchor["public_key_hex"]))
        signature = bytes.fromhex(envelope["signature_hex"])
        key.verify(signature, canonical(envelope["manifest"]))
    except (ValueError, TypeError, InvalidSignature):
        raise GovernanceError("release_signature_mismatch") from None
    blobs = _artifact_bytes(artifact_root, paths, manifest.artifact_digests)
    try:
        dataset = json.loads(blobs["dataset"])
        calibration = json.loads(blobs["calibration"])
        contracts = json.loads(blobs["contracts"])
        evidence = json.loads(blobs["evidence"])
    except (ValueError, UnicodeError):
        raise GovernanceError("invalid_artifact_json") from None
    verify_dataset_manifest(dataset)
    require(dataset["manifest_digest"] == manifest.dataset_manifest, "dataset_binding_mismatch")
    require(contracts == manifest.contract_versions, "contract_binding_mismatch")
    require({point: {"version": pin["version"], "digest": pin["digest"]}
             for point, pin in dataset["contract_pins"].items()} == manifest.contract_versions,
            "dataset_contract_binding_mismatch")
    require(isinstance(calibration, dict) and calibration.get("calibration_digest") == manifest.calibration_digest,
            "calibration_binding_mismatch")
    require(digest({key: value for key, value in calibration.items() if key != "calibration_digest"})
            == manifest.calibration_digest, "calibration_artifact_tampered")
    require(calibration.get("model_digest") == manifest.checkpoint_digest
            and calibration.get("dataset_manifest") == manifest.dataset_manifest, "calibration_bundle_mismatch")
    require(any(calibration.get("contract_version") == f"{point}:v{pin['version']}"
                and calibration.get("contract_digest") == pin["digest"]
                for point, pin in manifest.contract_versions.items()), "calibration_contract_mismatch")
    fields(evidence, {"candidate_checkpoint", "incumbent_checkpoint", "dataset_manifest", "holdout_digest",
                      "evaluator", "producer", "synthetic", "frozen", "sample_count", "metrics",
                      "incumbent_metrics", "red_team_pass", "uncertainty"}, "invalid_independent_evidence")
    identifier(evidence["evaluator"])
    identifier(evidence["producer"])
    require(evidence["evaluator"] != evidence["producer"], "self_evaluation_denied")
    require(evidence["candidate_checkpoint"] == manifest.checkpoint_digest
            and evidence["dataset_manifest"] == manifest.dataset_manifest
            and evidence["holdout_digest"] == digest(dataset["frozen_holdout"]), "evidence_binding_mismatch")
    sha256(evidence["incumbent_checkpoint"])
    require(evidence["frozen"] is True and type(evidence["synthetic"]) is bool and type(evidence["red_team_pass"]) is bool,
            "invalid_evidence_status")
    require(type(evidence["sample_count"]) is int and evidence["sample_count"] > 0
            and evidence["sample_count"] == len(dataset["splits"]["frozen_eval"]), "missing_independent_holdout")
    require(evidence["metrics"] == manifest.metrics and evidence["uncertainty"] == manifest.uncertainty,
            "evidence_metric_mismatch")
    fields(evidence["incumbent_metrics"], set(manifest.metrics), "incumbent_metrics_missing")
    for value in evidence["incumbent_metrics"].values():
        finite(value, 0, 1e12)
    if manifest.deployment_scope == "production":
        require(not dataset["synthetic"] and not evidence["synthetic"] and calibration.get("synthetic") is False,
                "synthetic_cannot_certify_production")
    return VerifiedRelease(manifest, dataset, evidence, manifest.release_digest, digest(dataset), digest(evidence))


def _assert_snapshot(release: VerifiedRelease) -> None:
    require(release.manifest.release_digest == release.verified_manifest_digest
            and digest(release.dataset) == release.verified_dataset_digest
            and digest(release.evidence) == release.verified_evidence_digest, "verified_snapshot_mutated")


def compare_shadow(release: VerifiedRelease, gate: dict) -> dict:
    _assert_snapshot(release)
    fields(gate, {"approved_evaluators", "minimum_samples", "metrics", "require_red_team"}, "invalid_gate")
    require(isinstance(gate["approved_evaluators"], list), "invalid_gate")
    require(type(gate["minimum_samples"]) is int and gate["minimum_samples"] > 0
            and type(gate["require_red_team"]) is bool, "invalid_gate")
    fields(gate["metrics"], set(release.manifest.metrics), "gate_metric_mismatch")
    evidence = release.evidence
    reasons = []
    if evidence["evaluator"] not in gate["approved_evaluators"]:
        reasons.append("unapproved_independent_evaluator")
    if evidence["sample_count"] < gate["minimum_samples"]:
        reasons.append("insufficient_holdout_population")
    if gate["require_red_team"] and not evidence["red_team_pass"]:
        reasons.append("red_team_gate_failed")
    for name, specification in gate["metrics"].items():
        fields(specification, {"direction", "threshold", "max_regression"}, "invalid_metric_gate")
        require(specification["direction"] in ("higher", "lower"), "invalid_metric_direction")
        threshold = finite(specification["threshold"], 0, 1e12)
        tolerance = finite(specification["max_regression"], 0, 1e12)
        value = release.manifest.metrics[name]
        incumbent = evidence["incumbent_metrics"][name]
        bounds = release.manifest.uncertainty[name]
        # Conservative bound must pass, not merely the point estimate.
        passed = (bounds[0] >= threshold and value >= incumbent - tolerance
                  if specification["direction"] == "higher"
                  else bounds[1] <= threshold and value <= incumbent + tolerance)
        if not passed:
            reasons.append("metric_gate_failed:" + name)
    return {"release_digest": release.manifest.release_digest, "passed": not reasons,
            "reasons": reasons, "synthetic": evidence["synthetic"], "automatic_promotion": False,
            "candidate": release.manifest.metrics, "incumbent": evidence["incumbent_metrics"]}


def operator_transition(*, action: str, operator: str, approved_operators: list[str],
                        current: VerifiedRelease | None, target: VerifiedRelease, gate: dict,
                        revoked: tuple[str, ...] = (), invalidated_datasets: tuple[str, ...] = ()) -> dict:
    """Return a complete checked local bundle selection. Does not activate a server.

    Callers must obtain both releases from verify_release immediately beforehand;
    CLI always does so. This is an operator workflow, not an authorization server.
    """
    _assert_snapshot(target)
    if current is not None:
        _assert_snapshot(current)
    identifier(operator)
    require(operator in approved_operators and operator not in {target.evidence["producer"], target.manifest.release_id},
            "operator_approval_required")
    require(action in ("shadow", "promote", "rollback"), "invalid_transition")
    require(target.manifest.release_digest not in revoked and target.manifest.dataset_manifest not in invalidated_datasets,
            "revoked_or_invalidated_release")
    require(current is not None or target.manifest.predecessor is None, "incumbent_bundle_required")
    if action == "promote":
        require(current is not None, "incumbent_required_for_promotion")
        require(target.manifest.deployment_scope == "production", "shadow_only_release")
    if action == "rollback":
        require(current is not None and current.manifest.predecessor == target.manifest.release_digest,
                "rollback_predecessor_mismatch")
        require(current.manifest.deployment_scope != "production" or target.manifest.deployment_scope == "production",
                "rollback_scope_mismatch")
    if current is not None and action != "rollback":
        require(target.manifest.predecessor == current.manifest.release_digest
                and target.evidence["incumbent_checkpoint"] == current.manifest.checkpoint_digest,
                "incumbent_bundle_mismatch")
    comparison = compare_shadow(target, gate)
    require(comparison["passed"], "independent_promotion_gate_failed")
    return {"schema_version": 1, "action": action, "operator": operator,
            "previous_release_digest": current.manifest.release_digest if current else None,
            "selected_release_digest": target.manifest.release_digest,
            "selected_bundle": asdict(target.manifest), "comparison": comparison,
            "serving_activated": False, "training_started": False}


def assess_drift(*, family: str, observed: dict, limits: dict, predecessor_available: bool) -> dict:
    require(family in ("router", "guard"), "invalid_drift_family")
    require(type(predecessor_available) is bool, "invalid_predecessor_status")
    required = {"outcome_error", "fallback_rate", "override_rate", "confidence_shift", "teacher_disagreement"}
    fields(observed, required, "invalid_drift_observations")
    fields(limits, required, "invalid_drift_limits")
    breaches = sorted(key for key in required if finite(observed[key]) > finite(limits[key]))
    action = "no_change"
    if breaches:
        action = "investigate_and_repeat_red_team" if family == "guard" else "controlled_retraining_proposal"
    return {"family": family, "breaches": breaches, "recommendation": action,
            "hold_promotion": bool(breaches), "rollback_recommended": bool(breaches and predecessor_available),
            "automatic_training": False, "privilege_relaxation": False, "automatic_rollback": False}


def check_reproducibility(reference: VerifiedRelease, rebuilt: VerifiedRelease) -> dict:
    _assert_snapshot(reference)
    _assert_snapshot(rebuilt)
    original, candidate = reference.manifest, rebuilt.manifest
    for key in ("dataset_manifest", "code_digest", "service_digest", "runtime", "hardware", "seed", "contract_versions", "reproducibility"):
        require(getattr(original, key) == getattr(candidate, key), "rebuild_provenance_mismatch")
    if original.reproducibility["mode"] == "exact_digest":
        passed = original.artifact_digests == candidate.artifact_digests
    else:
        passed = (set(original.metrics) == set(candidate.metrics)
                  and all(abs(original.metrics[key] - candidate.metrics[key]) <= tolerance
                          for key, tolerance in original.reproducibility["metric_tolerances"].items()))
    return {"mode": original.reproducibility["mode"], "within_declared_tolerance": passed,
            "reference_release": original.release_digest, "rebuilt_release": candidate.release_digest,
            "production_safety_certified": False}


def decision_bundle(release: VerifiedRelease):
    """Project verified pins into BE15's actual envelope; never install calibration.

    Unknown/new contract versions fail compatibility rather than silently mapping
    a release onto the currently installed decision menu.
    """
    from agent.decisions.contracts import DecisionError, ModelBundle
    from agent.decisions.registry import contract_for

    _assert_snapshot(release)
    for point, pin in release.manifest.contract_versions.items():
        try:
            contract = contract_for(point, pin["version"])
        except DecisionError:
            raise GovernanceError("runtime_contract_unavailable") from None
        require(contract.contract_digest == pin["digest"], "runtime_contract_digest_mismatch")
        for question in release.dataset["contract_pins"][point]["questions"]:
            try:
                contract.question(question)
            except DecisionError:
                raise GovernanceError("runtime_question_unavailable") from None
    return ModelBundle(model_digest=release.manifest.checkpoint_digest,
                       calibration_digest=release.manifest.calibration_digest,
                       service_digest=release.manifest.service_digest)
