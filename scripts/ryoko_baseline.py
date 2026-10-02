"""Reproduce the offline Ryoko fixture baseline without reading operator config.

The manifest hashes only checked-in inputs and synthetic configuration. Test
execution is delegated to the canonical runner; no production agent is started.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {
    "profile_scope": "tests/agent/test_secret_scope.py",
    "projects": "tests/tui_gateway/test_projects_rpc.py",
    "held_approval": "tests/agent/test_terminal_approval_batch.py",
    "provider_timeout": "tests/agent/test_fallback_429_after_timeout.py",
    "ownership": "tests/hermes_state/test_session_turn_lease.py",
    "lost_delivery": "tests/cron/test_delivery_queue.py",
    "compaction_restart": "tests/hermes_state/test_compression_watermark_commit.py",
    "unavailable_memory": "tests/agent/test_memory_provider.py",
}
SYNTHETIC_CONFIG = {
    "profiles": ["fixture-a", "fixture-b"],
    "projects": ["fixture-project-a", "fixture-project-b"],
    "providers": "test doubles only",
    "personal_memory": "unconfigured",
    "network_effects": False,
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def baseline_manifest(root: Path = ROOT) -> dict:
    dependencies = {name: digest((root / name).read_bytes()) for name in
                    ("pyproject.toml", "uv.lock", "package.json", "package-lock.json")}
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True).strip())
    return {
        "schema_version": 1,
        "fixture_version": 1,
        "source_sha": source_sha,
        "dirty_worktree": dirty,
        "dependencies": dependencies,
        "dependencies_digest": digest(json.dumps(dependencies, sort_keys=True).encode()),
        "config_source": "checked-in synthetic fixture specification; not operator config",
        "config_redacted_digest": digest(json.dumps(SYNTHETIC_CONFIG, sort_keys=True).encode()),
        "runtime_versions": {"python": platform.python_version(),
                             "python_target": (root / ".python-version").read_text().strip()},
        "os_backend": {"os": platform.system(), "architecture": platform.machine(),
                       "backend": "offline fixtures; no executor certified"},
        "feature_flags": {"ryoko_runtime": False, "personal_mcp": False, "laya": "off"},
        "observed_results": [{"scenario": name, "fixture": path,
                              "status": "not_run"} for name, path in FIXTURES.items()],
        "limits": ["No live provider, personal harness, external mutation or delivery",
                   "No hostile-code isolation or deployment certification",
                   "Existing profiles are not same-profile per-agent isolation",
                   "Crash-after-external-acceptance coverage is a BE06 target"],
    }


def run_fixtures(manifest: dict, root: Path = ROOT, timeout: float = 600) -> None:
    for result in manifest["observed_results"]:
        command = ["bash", "scripts/run_tests.sh", result["fixture"], "-j", "1"]
        result["command"] = command
        started = time.monotonic()
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True,
                                       timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(status="blocked", reason=type(exc).__name__)
        else:
            output = completed.stdout + completed.stderr
            result.update(status="passed" if completed.returncode == 0 else "failed",
                          returncode=completed.returncode, output_digest=digest(output))
            # Operator sees details, but manifests never copy full potentially
            # sensitive provider errors or host paths into published evidence.
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()
        result["duration_seconds"] = round(time.monotonic() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="run canonical offline fixtures")
    args = parser.parse_args()
    manifest = baseline_manifest()
    if args.run:
        run_fixtures(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return int(any(r["status"] in {"failed", "blocked"} for r in manifest["observed_results"]))


if __name__ == "__main__":
    raise SystemExit(main())
