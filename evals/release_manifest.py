"""Build an evidence-bound local release manifest; never authorizes a cutover.

This reads only repository files and explicitly selected validation receipts. It
does not inspect operator profiles, credentials, model endpoints or user stores.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import sys

from evals.release_pytest_receipts import safe_node_id

REQUIRED_LOCAL_GATES = (
    "release_slices", "authority_effect_memory", "generated_contracts",
    "shared_typecheck", "release_tooling", "release_lint", "reporting_integrity", "full_python_suite",
)
TEST_GATES = frozenset(REQUIRED_LOCAL_GATES) - {"shared_typecheck", "release_lint", "reporting_integrity"}
UNQUALIFIED = (
    "sensitive_ingestion_encryption_and_production_key_custody",
    "live_personal_memory_harness_schema_and_credentials",
    "live_provider_model_usage_cancellation_and_billing",
    "live_remote_connectors_browser_voice_and_delivery",
    "macos_windows_remote_executor_and_hostile_multitenant_isolation",
    "stochastic_quality_cost_latency_and_user_review_benefit",
    "LAYA_hardware_training_and_empirical_promotion",
    "FE13_user_journeys", "Dots_OD00_consumer_contract", "production_migration_and_cutover",
)
SUMMARY = re.compile(
    r"=== Summary: (?P<files>\d+) files, (?P<passed>\d+) tests passed, "
    r"(?P<failed>\d+) failed(?P<extra>.*?) in (?P<seconds>[\d.]+)s "
    r"\((?P<workers>\d+) workers\) ==="
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def log_receipt(path: Path, *, name: str, command: str, exit_code: int) -> dict:
    """Retain failures/skips and an exact log hash; zero collected is not green."""
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    matches = list(SUMMARY.finditer(text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one canonical runner summary")
    match = matches[0]
    counts = {key: int(match[key]) for key in ("files", "passed", "failed", "workers")}
    skipped = re.search(r"(\d+) skipped", match["extra"])
    crashed = re.search(r"(\d+) files? CRASHED", match["extra"])
    counts.update(skipped=int(skipped[1]) if skipped else 0, crashed_files=int(crashed[1]) if crashed else 0)
    passed = exit_code == 0 and counts["passed"] > 0 and counts["failed"] == counts["crashed_files"] == 0
    return {"name": name, "command": command, "exit_code": exit_code,
        "status": "passed" if passed else "failed", "counts": counts,
        "wall_seconds": float(match["seconds"]), "log_sha256": digest(data),
        "log_path": str(path), "failure_ids": sorted({safe_node_id(node)
            for node in re.findall(r"^FAILED (\S+)", text, re.M)}),
        "flaky_retry": "FLAKY file" in text,
        "note": "Skipped tests are not qualified; per-file discovery exclusions are separate."}


def qualification(runs: list[dict], *, implementation_gaps=()) -> dict:
    """Fail closed on absent/failed critical evidence and ambiguous duplicate IDs."""
    indexed = {}
    for run in runs:
        name = run.get("name")
        if not isinstance(name, str) or name in indexed:
            raise ValueError("Validation gate names must be unique strings")
        indexed[name] = run
    blocking = []
    for name in REQUIRED_LOCAL_GATES:
        run = indexed.get(name, {})
        counts = run.get("counts", {})
        valid = (run.get("status") == "passed" and run.get("exit_code") == 0
            and isinstance(run.get("command"), str) and bool(run["command"])
            and isinstance(run.get("log_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", run["log_sha256"]) is not None
            and not any(counts.get(key, 0) for key in ("failed", "crashed_files", "errors"))
            and (name not in TEST_GATES or counts.get("passed", 0) > 0)
            and not run.get("flaky_retry", False))
        if not valid:
            blocking.append(name)
    return {"local_candidate": "blocked" if blocking or implementation_gaps else "qualified_finite_local",
        "blocking_local_gates": blocking, "production_ready": False, "dots_ready": False,
        "blocking_implementation_gaps": list(implementation_gaps),
        "cutover_authorized": False, "unqualified_gates": list(UNQUALIFIED)}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _version(command: str) -> dict:
    try:
        run = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "unavailable", "reason": type(error).__name__}
    return {"status": "observed" if run.returncode == 0 else "unavailable",
        "exit_code": run.returncode, "version": run.stdout.strip()[:128]}


def source_inventory(root: Path) -> dict:
    """Hash candidate bytes, excluding documentation and receipts to avoid recursion."""
    names = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    files = {}
    for name in sorted(set(names)):
        path = root / name
        if not name or name.startswith("docs/") or path.suffix.lower() in {".md", ".rst"}:
            continue
        if path.is_file():
            files[name] = digest(path.read_bytes())
        else:
            files[name] = "missing"
    return {"head_commit": _git(root, "rev-parse", "HEAD"),
        "head_tree": _git(root, "rev-parse", "HEAD^{tree}"),
        "dirty_worktree": bool(_git(root, "status", "--porcelain")),
        "candidate_files_digest": digest(canonical(files)), "candidate_file_count": len(files),
        "digest_scope": "Tracked and nonignored candidate files, excluding docs/ and Markdown/rst; no private profile scan"}


def build_manifest(root: Path, receipt: dict) -> dict:
    from hermes_platform.host import facts
    from hermes_state_common import SCHEMA_VERSION
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    runs = receipt.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError("runs must be a list")
    deps = {name: digest((root / name).read_bytes()) for name in (
        "pyproject.toml", "uv.lock", "package.json", "package-lock.json")}
    contracts = {name: digest((root / name).read_bytes()) for name in (
        "apps/shared/src/gateway-contract.generated.ts", "apps/shared/src/gateway-contract.openrpc.json",
        "docs/build/release-contract-fixtures.json")}
    installed = sorted((dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions())
    config = {"fixture": "tests/integration/test_release_slices.py", "schema_version": 1,
        "credentials": "none", "personal_mcp": "absent_unconfigured",
        "laya": "off", "live_services": False}
    gaps = receipt.get("implementation_gaps", [])
    return {"schema_version": 1, "kind": "BE18_validation_repair_checkpoint",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "source_shas": source_inventory(root),
        "dependency_versions": {"files": deps, "digest": digest(canonical(deps)),
            "python": sys.version.split()[0], "pytest": importlib.metadata.version("pytest"),
            "installed_python_distribution_digest": digest(canonical(installed)),
            "installed_python_distribution_count": len(installed), "node": _version("node"), "npm": _version("npm")},
        "config": {"scope": "Synthetic checked-in fixture declaration, never operator config",
            "redacted": config, "digest": digest(canonical(config)), "config_version": DEFAULT_CONFIG["_config_version"]},
        "schema_versions": {"state_db": SCHEMA_VERSION, "runtime_protocol": 1, "contract_hashes": contracts},
        "supported_profiles": [{"os": facts.os_family(), "architecture": facts.native_arch(),
            "qualification": "finite local tests only", "executor": "bounded local adapters; see individual receipts",
            "provider": "fixture transport only; no live model qualified", "storage": "temporary SQLite and immutable local artifacts"}],
        "enabled_capabilities": ["owned_command_and_event_replay", "scoped_project_artifacts", "specialist_builtin_memory",
            "local_deterministic_mission_verification", "evaluated_local_workflows", "record_only_local_monitors"],
        "capability_activation": "Explicit synthetic strict-identity fixture only; no operator profile or default enabled state changed",
        "adapters": {"personal_memory": "unconfigured", "remote_delivery": "unqualified",
            "workflow": "local_deterministic_v1", "monitor": "normalized_text/json_fields/threshold",
            "external_mutations": "no new live adapter qualified"},
        "evaluation_receipts": runs, "qualification": qualification(runs, implementation_gaps=gaps),
        "implementation_gaps": gaps,
        "known_limits": receipt.get("known_limits", []) + list(UNQUALIFIED),
        "cross_repo": {"producer_contract_version": 1, "dots_adapter_commit": None,
            "compatibility_receipt": None, "frontend_FE13_receipt": None, "runtime_owner": "hermes_only_if_explicitly_admitted",
            "fixtures": "docs/build/release-contract-fixtures.json"},
        "rollback_bundle": {"runbook": "docs/build/release-runbook.md", "state_schema": SCHEMA_VERSION,
            "local_reopen_receipt": "release_slices", "full_profile_restore_qualified": False,
            "preconditions": ["pause admission and schedules", "reconcile active or unknown effects",
                "preserve immutable artifacts, consumed approvals and tombstones", "retain schema-compatible readers",
                "verify sole runtime_owner before resuming"], "old_prompt_replay_allowed": False},
        "publication": {"remote_candidate_commit": None, "ci_status": "parent_must_verify_after_publication"}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    report = build_manifest(args.root.resolve(), json.loads(args.receipt.read_text()))
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
