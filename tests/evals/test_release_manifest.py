"""Release decisions fail closed; fixtures bind the current producer schema."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.release_manifest import REQUIRED_LOCAL_GATES, digest, log_receipt, qualification


def passing_runs():
    return [{"name": name, "status": "passed", "exit_code": 0,
        "command": "synthetic test command", "log_sha256": "a" * 64,
        "counts": {"passed": 1, "failed": 0}} for name in REQUIRED_LOCAL_GATES]


def test_local_evidence_never_implies_production_or_dots_and_missing_gate_blocks():
    runs = passing_runs()
    qualified = qualification(runs)
    assert qualified["local_candidate"] == "qualified_finite_local"
    assert qualified["production_ready"] is qualified["dots_ready"] is qualified["cutover_authorized"] is False
    incomplete = qualification(runs, implementation_gaps=["template_application"])
    assert incomplete["local_candidate"] == "blocked"
    assert incomplete["blocking_implementation_gaps"] == ["template_application"]
    for target in REQUIRED_LOCAL_GATES:
        for mutation in ({"status": "blocked"}, {"exit_code": 1}, {"log_sha256": ""},
                         {"counts": {"failed": 1}}, {"flaky_retry": True}):
            modified = deepcopy(runs)
            next(row for row in modified if row["name"] == target).update(mutation)
            assert target in qualification(modified)["blocking_local_gates"]
        assert target in qualification([row for row in runs if row["name"] != target])["blocking_local_gates"]
    with pytest.raises(ValueError, match="unique"):
        qualification(runs + [runs[0]])


def test_canonical_log_receipt_retains_skip_failure_and_crash_evidence(tmp_path):
    path = tmp_path / "runner.log"
    data = ("FAILED tests/fixture.py::test_boundary - reason\n"
        "=== Summary: 3 files, 9 tests passed, 1 failed, 1 file CRASHED, 2 skipped (100% complete) in 1.3s (4 workers) ===\n")
    path.write_text(data)
    receipt = log_receipt(path, name="release_slices", command="scripts/run_tests.sh fixture", exit_code=1)
    assert receipt["status"] == "failed"
    assert receipt["counts"] == {"files": 3, "passed": 9, "failed": 1, "crashed_files": 1, "skipped": 2, "workers": 4}
    assert receipt["failure_ids"] == ["tests/fixture.py::test_boundary"]
    assert receipt["log_sha256"] == digest(data.encode())
    path.write_text("=== Summary: 1 files, 0 tests passed, 0 failed (100% complete) in 0.1s (1 workers) ===")
    assert log_receipt(path, name="empty", command="runner", exit_code=0)["status"] == "failed"


def test_cross_repo_examples_validate_current_types_and_reject_client_authority():
    from pydantic import ValidationError
    from tui_gateway.contracts.runtime_v1 import (
        CommandReceipt, RuntimeCommandParams, RuntimeEventEnvelope, RuntimeEventsSinceParams,
    )
    from tui_gateway.contracts.artifacts import ArtifactVersionRef
    from tui_gateway.contracts.runtime_results import RuntimeDeliveryAckParams

    fixture = json.loads((Path(__file__).resolve().parents[2] / "docs/build/release-contract-fixtures.json").read_text())
    models = {"command": RuntimeCommandParams, "receipt": CommandReceipt,
        "replay_request": RuntimeEventsSinceParams, "event": RuntimeEventEnvelope,
        "artifact_ref": ArtifactVersionRef, "delivery_ack": RuntimeDeliveryAckParams}
    for key, model in models.items():
        model.model_validate(fixture["examples"][key])
    for key in ("principal_id", "agent_id", "profile", "identity_binding", "provider"):
        with pytest.raises(ValidationError):
            RuntimeCommandParams.model_validate({**fixture["examples"]["command"], key: "forged"})
    assert fixture["consumer_validation"]["dots_adapter_commit"] is None
    assert fixture["consumer_validation"]["OD00"] == "not_run"


def test_reporting_plugin_keeps_original_reports_and_exit_status_without_private_text(tmp_path):
    from evals.release_pytest_receipts import ReleaseReceipts
    reporter = ReleaseReceipts(tmp_path / "receipts")
    reports = [SimpleNamespace(nodeid="tests/fixture.py::test_case[private fixture value]",
        outcome=outcome, when=when, longrepr=reason) for outcome, when, reason in (
            ("passed", "call", None), ("skipped", "setup", "requires Windows; private fixture value"),
            ("failed", "call", "AssertionError: private fixture value"))]
    for report in reports:
        before = deepcopy(vars(report))
        assert reporter.pytest_runtest_logreport(report) is None
        assert vars(report) == before
    session = SimpleNamespace(testscollected=3, config=SimpleNamespace(args=["tests/fixture.py"]))
    assert reporter.pytest_sessionfinish(session, 1) is None
    output = next((tmp_path / "receipts").glob("*.json")).read_text()
    receipt = json.loads(output)
    assert receipt["exit_code"] == 1 and receipt["counts"]["failed"] == 1
    assert receipt["counts"]["passed"] == receipt["counts"]["skipped"] == 1
    assert "private fixture value" not in output
    assert {row["classification"] for row in receipt["exceptions"]} == {"non_host_platform", "test_failure"}


@pytest.mark.parametrize("exit_code", [0, 1])
def test_reporting_disk_full_preserves_true_test_exit_status(tmp_path, monkeypatch, exit_code):
    import errno
    from evals.release_pytest_receipts import ReleaseReceipts
    reporter = ReleaseReceipts(tmp_path / "receipts")
    session = SimpleNamespace(testscollected=1, config=SimpleNamespace(args=["tests/fixture.py"]), exitstatus=exit_code)
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "synthetic full reporting filesystem")
    monkeypatch.setattr(Path, "write_text", full)
    assert reporter.pytest_sessionfinish(session, exit_code) is None
    assert session.exitstatus == exit_code
    assert reporter.write_error == {"errno": errno.ENOSPC, "exit_code_preserved": exit_code}
    assert list((tmp_path / "receipts").glob("*.json")) == []
