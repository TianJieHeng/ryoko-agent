"""Offline consent, split, redaction and deletion gates for decision datasets.

Inputs and operator approvals are local files. Nothing discovers receipts, exports
user memory, calls a teacher, trains weights, or configures a serving process.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


class GovernanceError(ValueError):
    """Only fixed codes, never payloads or secret-bearing paths, reach diagnostics."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GovernanceError(code)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise GovernanceError("invalid_json") from None


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def identifier(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None,
            "invalid_identifier")
    return value


def sha256(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid_digest")
    return value


def fields(value: Any, expected: set[str], code: str) -> None:
    require(isinstance(value, dict) and set(value) == expected, code)


def timestamp(value: Any) -> datetime:
    require(isinstance(value, str), "invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GovernanceError("invalid_timestamp") from None
    require(result.tzinfo is not None, "timestamp_requires_timezone")
    return result


def finite(value: Any, minimum: float = 0, maximum: float = 1) -> float:
    require(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum,
            "invalid_number")
    return float(value)


@dataclass(frozen=True)
class DatasetRecord:
    source_receipt: str
    source_id: str
    episode_id: str
    task_id: str
    observed_at: str
    consent_purpose: tuple[str, ...]
    scope: str
    provenance: dict
    redacted_packet: dict
    independent_label: str
    label_source: str
    labeler: str
    producer: str
    contract_version: str
    contract_digest: str
    question_id: str
    deletion_state: str = "active"

    @classmethod
    def parse(cls, raw: dict) -> DatasetRecord:
        fields(raw, set(cls.__dataclass_fields__), "invalid_record_fields")
        for key in ("source_receipt", "source_id", "episode_id", "task_id", "scope",
                    "independent_label", "labeler", "producer", "contract_version", "question_id"):
            identifier(raw[key])
        timestamp(raw["observed_at"])
        sha256(raw["contract_digest"])
        require(re.fullmatch(r"DP(?:0[1-9]|1[0-6]):v[1-9][0-9]*", raw["contract_version"]) is not None,
                "invalid_contract_version")
        purposes = raw["consent_purpose"]
        require(isinstance(purposes, list) and purposes and len(purposes) == len(set(purposes)),
                "invalid_consent_purpose")
        for purpose in purposes:
            identifier(purpose)
        require(raw["deletion_state"] in ("active", "deleted", "withdrawn"), "invalid_deletion_state")
        provenance = raw["provenance"]
        fields(provenance, {"kind", "license", "source_version"}, "invalid_provenance")
        require(provenance["kind"] in ("synthetic", "public", "private"), "invalid_source_kind")
        identifier(provenance["license"])
        identifier(provenance["source_version"])
        require(raw["label_source"] in ("human", "independent_teacher", "deterministic_outcome", "synthetic_fixture"),
                "unreviewed_self_label_denied")
        require(raw["labeler"] != raw["producer"], "self_label_denied")
        require(raw["label_source"] != "synthetic_fixture" or provenance["kind"] == "synthetic",
                "synthetic_label_misclassified")
        packet = raw["redacted_packet"]
        fields(packet, {"authority", "live_options", "state"}, "invalid_packet")
        require(isinstance(packet["authority"], dict) and packet["authority"], "authority_required")
        require(isinstance(packet["state"], dict), "invalid_packet_state")
        menu = packet["live_options"]
        require(isinstance(menu, list) and 2 <= len(menu) <= 64 and len(set(menu)) == len(menu)
                and "unclear" in menu, "invalid_live_menu")
        for choice in menu:
            identifier(choice)
        require(raw["independent_label"] in menu, "label_not_in_live_menu")
        detached = json.loads(canonical(raw))
        detached["consent_purpose"] = tuple(purposes)
        return cls(**detached)


@dataclass(frozen=True)
class DatasetPolicy:
    purpose: str
    scope: str
    train_before: str
    calibration_before: str
    packet_byte_limit: int = 16384

    def __post_init__(self):
        identifier(self.purpose)
        identifier(self.scope)
        require(timestamp(self.train_before) < timestamp(self.calibration_before), "invalid_time_boundaries")
        require(type(self.packet_byte_limit) is int and 512 <= self.packet_byte_limit <= 16384,
                "invalid_packet_byte_limit")

    def split(self, observed_at: str) -> str:
        instant = timestamp(observed_at)
        if instant < timestamp(self.train_before):
            return "train"
        if instant < timestamp(self.calibration_before):
            return "calibration"
        return "frozen_eval"


def redact_packet(packet: dict, seeded_secrets: tuple[str, ...], byte_limit: int) -> dict:
    """Preserve menu ordering and delimiters; refuse authority loss or truncation.

    Seed removal and obvious credential patterns are useful safeguards, not a
    proof that arbitrary private content is anonymous or safe to export.
    """
    require(all(isinstance(value, str) and value for value in seeded_secrets), "invalid_secret_seed")
    seeds = sorted(set(seeded_secrets), key=len, reverse=True)
    pattern = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|(?:bearer|password|api[_-]?key|token)\s*[:= ]\s*[^\s,;\"}]+)")

    def scrub(value):
        if isinstance(value, str):
            for secret in seeds:
                value = value.replace(secret, "[REDACTED]")
            return pattern.sub("[REDACTED]", value)
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            require(all(isinstance(key, str) and scrub(key) == key for key in value), "secret_in_packet_key")
            return {key: scrub(item) for key, item in value.items()}
        require(value is None or type(value) in (bool, int, float), "invalid_packet_value")
        return value

    result = scrub(packet)
    require(result["authority"] == packet["authority"] and result["live_options"] == packet["live_options"],
            "redaction_would_change_authority")
    require(len(canonical(result)) <= byte_limit, "packet_too_large_no_truncation")
    return result


def _private_approval(record: DatasetRecord, policy: DatasetPolicy, approvals: list[dict], destination: Path,
                      *, packets: bool) -> None:
    if record.provenance["kind"] != "private":
        return
    for approval in approvals:
        fields(approval, {"approval_id", "source_ids", "purpose", "scope", "destination", "include_packets"},
               "invalid_export_approval")
        identifier(approval["approval_id"])
        require(isinstance(approval["source_ids"], list) and type(approval["include_packets"]) is bool,
                "invalid_export_approval")
        if (record.source_id in approval["source_ids"] and approval["purpose"] == policy.purpose
                and approval["scope"] == policy.scope and Path(approval["destination"]).resolve() == destination.resolve()
                and (not packets or approval["include_packets"])):
            return
    raise GovernanceError("private_export_requires_bounded_approval")


@dataclass(frozen=True)
class DatasetBuild:
    manifest: dict
    records: tuple[dict, ...]

    def export(self, *, include_packets: bool = False) -> dict:
        require(not include_packets or self.manifest["packet_export_approved"], "packet_export_not_approved")
        rows = [dict(row) for row in self.records]
        if not include_packets:
            rows = [{key: value for key, value in row.items() if key != "redacted_packet"} for row in rows]
        return json.loads(canonical({"manifest": self.manifest, "records": rows, "contains_packets": include_packets}))


def build_dataset(raw_records: list[dict], policy: DatasetPolicy, *, destination: Path,
                  approvals: list[dict] | None = None, seeded_secrets: tuple[str, ...] = (),
                  deleted_sources: tuple[str, ...] = (), previous_manifest: dict | None = None,
                  include_packets: bool = False) -> DatasetBuild:
    """Deterministically rebuild from live sources; never mutate frozen holdout membership.

    Tasks, episodes and duplicate packet content cannot cross time partitions.
    Previous holdouts can lose deleted records, but never be relabeled or extended.
    """
    require(isinstance(raw_records, list) and len(raw_records) <= 100000, "invalid_record_collection")
    records = [DatasetRecord.parse(row) for row in raw_records]
    require(len({row.source_receipt for row in records}) == len(records), "duplicate_source_receipt")
    removed = {digest(identifier(source)) for source in deleted_sources}
    removed.update(digest(row.source_id) for row in records if row.deletion_state != "active")
    if previous_manifest is not None:
        verify_dataset_manifest(previous_manifest)
        require(previous_manifest["policy"] == asdict(policy), "frozen_policy_changed")
        removed.update(previous_manifest["deleted_sources"])
        for heldout in previous_manifest["frozen_holdout"]:
            if removed.intersection(heldout["source_digests"]):
                removed.update(heldout["source_digests"])
    rows = []
    partition_keys = {}
    duplicates = {}
    for record in sorted(records, key=lambda row: row.source_receipt):
        source_digest = digest(record.source_id)
        if source_digest in removed:
            continue
        require(policy.purpose in record.consent_purpose and record.scope == policy.scope, "consent_scope_mismatch")
        _private_approval(record, policy, approvals or [], destination, packets=include_packets)
        metadata = asdict(record)
        metadata.pop("redacted_packet")
        require(not any(secret in canonical(metadata).decode() for secret in seeded_secrets), "secret_in_metadata")
        packet = redact_packet(record.redacted_packet, seeded_secrets, policy.packet_byte_limit)
        split = policy.split(record.observed_at)
        content_digest = digest(packet)
        for namespace, value in (("task", record.task_id), ("episode", record.episode_id), ("content", content_digest)):
            key = (namespace, value)
            require(key not in partition_keys or partition_keys[key] == split, "split_leakage_denied")
            partition_keys[key] = split
        row = {"source_digest": source_digest, "receipt_digest": digest(record.source_receipt),
               "episode_digest": digest(record.episode_id), "task_digest": digest(record.task_id),
               "observed_at": record.observed_at, "consent_purpose": sorted(record.consent_purpose),
               "scope": record.scope, "provenance": record.provenance,
               "redacted_packet": packet, "packet_digest": digest(packet), "live_options": list(packet["live_options"]),
               "independent_label": record.independent_label, "label_source": record.label_source,
               "labeler_digest": digest(record.labeler), "producer_digest": digest(record.producer),
               "contract_version": record.contract_version, "contract_digest": record.contract_digest,
               "question_id": record.question_id, "split": split, "deletion_state": "active"}
        duplicate_key = (content_digest, record.contract_version, record.contract_digest, record.question_id, split)
        if duplicate_key in duplicates:
            old = duplicates[duplicate_key]
            require(old["independent_label"] == row["independent_label"], "conflicting_duplicate_label")
            # Keep all source lineage: deleting either contributor invalidates dependent builds.
            for name, item in (("source_digests", source_digest), ("episode_digests", row["episode_digest"]),
                               ("task_digests", row["task_digest"])):
                old[name] = sorted(set(old[name] + [item]))
            continue
        row["source_digests"] = [source_digest]
        row["episode_digests"] = [row["episode_digest"]]
        row["task_digests"] = [row["task_digest"]]
        duplicates[duplicate_key] = row
        rows.append(row)
    for row in rows:
        row["record_digest"] = digest({key: value for key, value in row.items() if key != "redacted_packet"})
    rows.sort(key=lambda row: row["record_digest"])
    holdout = [{key: row[key] for key in ("record_digest", "source_digests", "episode_digests", "task_digests", "packet_digest")}
               for row in rows if row["split"] == "frozen_eval"]
    if previous_manifest is not None:
        previous = previous_manifest["frozen_holdout"]
        allowed = [row for row in previous if not (set(row["source_digests"]) & removed)]
        require(holdout == allowed, "frozen_holdout_contamination_denied")
        protected = {value for row in previous for value in (*row["episode_digests"], *row["task_digests"], row["packet_digest"])}
        require(all(not protected.intersection({*row["episode_digests"], *row["task_digests"], row["packet_digest"]})
                    for row in rows if row["split"] != "frozen_eval"), "frozen_holdout_reuse_denied")
    contract_pins = {}
    for row in rows:
        point, version = row["contract_version"].split(":v")
        pin = contract_pins.setdefault(point, {"version": int(version), "digest": row["contract_digest"], "questions": []})
        require(pin["version"] == int(version) and pin["digest"] == row["contract_digest"], "dataset_contract_pin_conflict")
        pin["questions"] = sorted(set(pin["questions"] + [row["question_id"]]))
    body = {"schema_version": 1, "policy": asdict(policy), "contract_pins": contract_pins, "packet_export_approved": include_packets,
            "record_digests": [row["record_digest"] for row in rows],
            "source_digests": sorted({source for row in rows for source in row["source_digests"]}),
            "splits": {split: [row["record_digest"] for row in rows if row["split"] == split]
                       for split in ("train", "calibration", "frozen_eval")},
            "frozen_holdout": holdout, "deleted_sources": sorted(removed),
            "synthetic": any(row["provenance"]["kind"] == "synthetic" for row in rows),
            "previous_manifest_digest": previous_manifest["manifest_digest"] if previous_manifest else None,
            "deleted_weights_unlearned": False}
    if previous_manifest is not None:
        previous_body = {key: value for key, value in previous_manifest.items() if key != "manifest_digest"}
        comparable = {key: value for key, value in body.items() if key != "previous_manifest_digest"}
        if comparable == {key: value for key, value in previous_body.items() if key != "previous_manifest_digest"}:
            return DatasetBuild(previous_manifest, tuple(rows))
    return DatasetBuild({**body, "manifest_digest": digest(body)}, tuple(rows))


def verify_dataset_manifest(manifest: dict) -> None:
    expected = {"schema_version", "policy", "contract_pins", "packet_export_approved", "record_digests", "source_digests", "splits",
                "frozen_holdout", "deleted_sources", "synthetic", "previous_manifest_digest", "deleted_weights_unlearned",
                "manifest_digest"}
    fields(manifest, expected, "invalid_dataset_manifest")
    require(manifest["schema_version"] == 1 and manifest["deleted_weights_unlearned"] is False,
            "invalid_dataset_manifest")
    require(type(manifest["synthetic"]) is bool and type(manifest["packet_export_approved"]) is bool,
            "invalid_dataset_manifest")
    require(isinstance(manifest["frozen_holdout"], list), "invalid_dataset_manifest")
    for key in ("record_digests", "source_digests", "deleted_sources"):
        require(isinstance(manifest[key], list) and len(set(manifest[key])) == len(manifest[key]), "invalid_dataset_manifest")
        for value in manifest[key]:
            sha256(value)
    fields(manifest["splits"], {"train", "calibration", "frozen_eval"}, "invalid_dataset_splits")
    require(all(isinstance(partition, list) for partition in manifest["splits"].values()), "invalid_dataset_splits")
    flattened = [item for partition in manifest["splits"].values() for item in partition]
    require(len(set(flattened)) == len(flattened) and set(flattened) == set(manifest["record_digests"]),
            "dataset_split_overlap")
    require([row["record_digest"] for row in manifest["frozen_holdout"]] == manifest["splits"]["frozen_eval"],
            "invalid_frozen_holdout")
    DatasetPolicy(**manifest["policy"])
    sha256(manifest["manifest_digest"])
    require(digest({key: value for key, value in manifest.items() if key != "manifest_digest"}) == manifest["manifest_digest"],
            "dataset_manifest_tampered")


def deletion_impact(manifests: list[dict], releases: list[dict], deleted_sources: list[str]) -> dict:
    deleted = {digest(identifier(source)) for source in deleted_sources}
    for manifest in manifests:
        verify_dataset_manifest(manifest)
    affected = sorted(manifest["manifest_digest"] for manifest in manifests if deleted.intersection(manifest["source_digests"]))
    checkpoints = sorted({sha256(release["checkpoint_digest"]) for release in releases
                          if release["dataset_manifest"] in affected})
    return {"schema_version": 1, "deleted_sources": sorted(deleted), "invalidated_dataset_manifests": affected,
            "checkpoint_review_required": checkpoints, "freeze_exports_and_promotion": bool(affected),
            "future_exports_must_rebuild": True, "unlearning_claim": False}


def refit_calibration(build: DatasetBuild, observations: list[dict], *, model_digest: str,
                      temperatures: tuple[float, ...] = (.5, .75, 1., 1.5, 2., 3.)) -> dict:
    """Fit only a scalar temperature on an independent calibration partition.

    This produces an offline calibration artifact, not a trained candidate,
    measured holdout, deployment approval or probability-safety guarantee.
    """
    sha256(model_digest)
    verify_build(build)
    require(temperatures and all(finite(value, .01, 100) for value in temperatures), "invalid_temperature_grid")
    rows = {row["record_digest"]: row for row in build.records if row["split"] == "calibration"}
    require(rows and len(observations) == len(rows), "calibration_population_mismatch")
    seen = set()
    checked = []
    for observation in observations:
        fields(observation, {"record_digest", "model_digest", "distribution"}, "invalid_calibration_observation")
        key = observation["record_digest"]
        require(key in rows and key not in seen, "calibration_split_leakage")
        require(observation["model_digest"] == model_digest, "calibration_model_mismatch")
        seen.add(key)
        row = rows[key]
        distribution = observation["distribution"]
        fields(distribution, set(row["live_options"]), "calibration_options_mismatch")
        for probability in distribution.values():
            finite(probability, 1e-12, 1)
        require(math.isclose(math.fsum(distribution.values()), 1., abs_tol=1e-9, rel_tol=0), "invalid_distribution")
        checked.append((key, row["independent_label"], distribution))
    checked.sort()
    require(len({(row["contract_version"], row["contract_digest"], row["question_id"]) for row in rows.values()}) == 1, "calibration_contract_mismatch")

    def loss(temperature):
        losses = []
        for _, label, distribution in checked:
            logits = {key: math.log(value) / temperature for key, value in distribution.items()}
            maximum = max(logits.values())
            logsum = maximum + math.log(math.fsum(math.exp(value - maximum) for value in logits.values()))
            losses.append(logsum - logits[label])
        return math.fsum(losses) / len(losses)

    fitted = min(sorted(set(temperatures)), key=lambda value: (loss(value), abs(value - 1), value))
    body = {"schema_version": 1, "kind": "offline_temperature_refit", "model_digest": model_digest,
            "dataset_manifest": build.manifest["manifest_digest"], "record_digests": sorted(rows),
            "contract_version": next(iter(rows.values()))["contract_version"],
            "contract_digest": next(iter(rows.values()))["contract_digest"],
            "question_id": next(iter(rows.values()))["question_id"], "temperature": fitted,
            "grid": sorted(set(temperatures)), "calibration_nll": loss(fitted),
            "observations_digest": digest(sorted(observations, key=lambda item: item["record_digest"])), "synthetic": build.manifest["synthetic"],
            "production_qualified": False, "trained_model": False}
    return {**body, "calibration_digest": digest(body)}


def verify_build(build: DatasetBuild) -> None:
    verify_dataset_manifest(build.manifest)
    identifiers = []
    splits = {name: [] for name in ("train", "calibration", "frozen_eval")}
    for row in build.records:
        require(isinstance(row, dict), "invalid_build_record")
        required = {"source_digest", "receipt_digest", "episode_digest", "task_digest", "observed_at",
                    "consent_purpose", "scope", "provenance", "packet_digest", "live_options",
                    "independent_label", "label_source", "labeler_digest", "producer_digest", "contract_version", "contract_digest", "question_id",
                    "split", "deletion_state", "source_digests", "episode_digests", "task_digests", "record_digest"}
        fields({key: value for key, value in row.items() if key != "redacted_packet"}, required,
               "invalid_build_record")
        receipt = {key: value for key, value in row.items() if key not in ("redacted_packet", "record_digest")}
        require(digest(receipt) == row["record_digest"], "build_record_tampered")
        if "redacted_packet" in row:
            require(digest(row["redacted_packet"]) == row["packet_digest"], "build_packet_tampered")
        require(row["split"] in splits, "invalid_build_split")
        require(not set(row["source_digests"]).intersection(build.manifest["deleted_sources"]), "deleted_record_in_build")
        identifiers.append(row["record_digest"])
        splits[row["split"]].append(row["record_digest"])
    require(identifiers == build.manifest["record_digests"] and len(set(identifiers)) == len(identifiers)
            and splits == build.manifest["splits"], "build_manifest_mismatch")


def load_build(payload: dict) -> DatasetBuild:
    fields(payload, {"manifest", "records", "contains_packets"}, "invalid_build_export")
    require(isinstance(payload["records"], list) and type(payload["contains_packets"]) is bool,
            "invalid_build_export")
    require(all(("redacted_packet" in row) == payload["contains_packets"] for row in payload["records"]),
            "invalid_build_export")
    build = DatasetBuild(payload["manifest"], tuple(payload["records"]))
    verify_build(build)
    return build
