"""Offline baseline evidence does not import config or expose environment secrets."""
import json
import subprocess
import sys
from pathlib import Path

from scripts.ryoko_baseline import baseline_manifest, digest


def test_manifest_uses_synthetic_configuration_and_binds_dependencies(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-sensitive-value")
    manifest = baseline_manifest()
    assert "fixture-sensitive-value" not in json.dumps(manifest)
    expected = digest(json.dumps(manifest["dependencies"], sort_keys=True).encode())
    assert manifest["dependencies_digest"] == expected
    assert all(row["status"] == "not_run" for row in manifest["observed_results"])
    root = Path(__file__).resolve().parents[2]
    assert all((root / row["fixture"]).is_file() for row in manifest["observed_results"])


def test_cli_writes_explicit_unmeasured_manifest(tmp_path):
    output = tmp_path / "manifest.json"
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, str(root / "scripts/ryoko_baseline.py"),
                                "--output", str(output)], cwd=root, check=False)
    assert completed.returncode == 0
    manifest = json.loads(output.read_text())
    assert manifest["source_sha"]
    assert not manifest["feature_flags"]["personal_mcp"]
    assert all(row["status"] == "not_run" for row in manifest["observed_results"])
