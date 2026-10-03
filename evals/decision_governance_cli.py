"""Local governed dataset/release CLI. Never discovers or exports live user receipts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from evals.decision_governance import (
    DatasetPolicy, GovernanceError, build_dataset, canonical, deletion_impact,
    load_build, refit_calibration, require,
)
from evals.decision_governance_release import (
    assess_drift, check_reproducibility, compare_shadow, decision_bundle, operator_transition, verify_release,
)


def read_json(path: str | Path):
    target = Path(path)
    require(target.stat().st_size <= 32 * 1024 * 1024, "input_size_limit")
    return json.loads(target.read_text(encoding="utf-8"))


def write_json(path: str | Path, value) -> None:
    """Atomic replacement avoids partial governance receipts; no permissions changes."""
    target = Path(path)
    require(target.parent.is_dir() and not target.is_symlink(), "invalid_output_destination")
    with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=".governance-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _build(args):
    ledger_path = Path(args.ledger)
    previous = read_json(ledger_path) if ledger_path.exists() else None
    deleted = read_json(args.deleted_sources) if args.deleted_sources else []
    seeds = read_json(args.secret_seeds) if args.secret_seeds else []
    require(isinstance(seeds, list) and isinstance(deleted, list), "invalid_redaction_or_deletion_input")
    build = build_dataset(read_json(args.input), DatasetPolicy(**read_json(args.policy)),
                          destination=Path(args.output), approvals=read_json(args.approvals) if args.approvals else [],
                          seeded_secrets=tuple(seeds), deleted_sources=tuple(deleted), previous_manifest=previous,
                          include_packets=args.include_packets)
    require(Path(args.output).resolve() != ledger_path.resolve(), "output_overlaps_ledger")
    # Freeze first. An interrupted export can then be rerun against identical membership.
    write_json(ledger_path, build.manifest)
    write_json(args.output, build.export(include_packets=args.include_packets))
    return {"action": "build", "manifest_digest": build.manifest["manifest_digest"],
            "record_count": len(build.records), "contains_packets": args.include_packets,
            "trained_model": False}


def _refit(args):
    result = refit_calibration(load_build(read_json(args.input)), read_json(args.observations),
                               model_digest=args.model_digest)
    write_json(args.output, result)
    return {"action": "calibration_refit", "calibration_digest": result["calibration_digest"],
            "trained_model": False, "production_qualified": False}


def _deletion(args):
    payload = read_json(args.input)
    result = deletion_impact(payload["manifests"], payload["releases"], payload["deleted_sources"])
    write_json(args.output, result)
    return result


def _verify_bundle(specification, trust, invalidated):
    return verify_release(read_json(specification["envelope"]), trust=trust,
                          artifact_root=Path(specification["artifact_root"]), paths=specification["paths"],
                          invalidated_datasets=tuple(invalidated))


def _release(args):
    config = read_json(args.input)
    trust = read_json(args.trust)
    invalidated = config.get("invalidated_datasets", [])
    target = _verify_bundle(config["target"], trust, invalidated)
    current = _verify_bundle(config["current"], trust, []) if config.get("current") else None
    def projection():
        from dataclasses import asdict
        return {"bundle": asdict(decision_bundle(target)), "runtime_contracts_compatible": True,
                "calibrator_installed": False, "serving_activated": False}

    def reproduce():
        require(current is not None, "reference_release_required")
        return check_reproducibility(current, target)

    def transition():
        return operator_transition(action=args.action, operator=args.operator,
                                   approved_operators=read_json(args.operators), current=current, target=target,
                                   gate=config["gate"], revoked=tuple(config.get("revoked", [])),
                                   invalidated_datasets=tuple(invalidated))

    actions = {
        "verify": lambda: {"verified": True, "release_digest": target.manifest.release_digest,
                           "scope": target.manifest.deployment_scope, "signing_proves_safety": False},
        "runtime-projection": projection,
        "compare": lambda: compare_shadow(target, config["gate"]),
        "reproduce": reproduce,
        "shadow": transition,
        "promote": transition,
        "rollback": transition,
    }
    result = actions[args.action]()
    write_json(args.output, result)
    return {key: result[key] for key in result if key not in ("selected_bundle", "comparison")}


def _drift(args):
    result = assess_drift(**read_json(args.input))
    write_json(args.output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild approved records; receipt-only export by default")
    for name in ("input", "policy", "output", "ledger"):
        build.add_argument("--" + name, required=True)
    for name in ("approvals", "secret-seeds", "deleted-sources"):
        build.add_argument("--" + name)
    build.add_argument("--include-packets", action="store_true")
    refit = sub.add_parser("refit", help="fit offline temperature on calibration receipts only")
    for name in ("input", "observations", "model-digest", "output"):
        refit.add_argument("--" + name, required=True)
    deletion = sub.add_parser("deletion-impact", help="invalidate manifests and flag checkpoint review")
    release = sub.add_parser("release", help="verify signatures/pins and select a local bundle without serving activation")
    release.add_argument("--action", required=True, choices=("verify", "runtime-projection", "compare", "reproduce", "shadow", "promote", "rollback"))
    release.add_argument("--trust", required=True, help="existing operator-approved Ed25519 public identity registry")
    release.add_argument("--operator")
    release.add_argument("--operators", help="existing independently controlled approved operator registry")
    drift = sub.add_parser("drift", help="emit review/rollback recommendations without training")
    for command in (deletion, release, drift):
        command.add_argument("--input", required=True)
        command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "release" and args.action in ("shadow", "promote", "rollback"):
        require_arguments = args.operator and args.operators
        if not require_arguments:
            parser.error("operator and operators are required for transitions")
    protected = [getattr(args, key, None) for key in ("input", "policy", "secret_seeds", "deleted_sources", "approvals",
                                                    "observations", "trust", "operators")]
    if any(value and Path(value).resolve() == Path(args.output).resolve() for value in protected):
        parser.error("output must not replace an input")
    if args.command == "build" and any(value and Path(value).resolve() == Path(args.ledger).resolve() for value in protected):
        parser.error("ledger must not replace an input")
    handlers = {"build": _build, "refit": _refit, "deletion-impact": _deletion, "release": _release, "drift": _drift}
    try:
        result = handlers[args.command](args)
    except GovernanceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "error": "invalid_or_unavailable_local_input"}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
