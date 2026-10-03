"""Read-only pytest outcome receipts for canonical per-file release runs.

Enable explicitly with ``-p evals.release_pytest_receipts --release-receipts=DIR``.
No assertion, skip, collection or exit status is altered. Private assertion text
and parameter values are not written; only bounded classifications and hashes are.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid


def pytest_addoption(parser):
    parser.addoption("--release-receipts", default=None,
        help="Write per-process sanitized release outcome receipts to this directory")


def pytest_configure(config):
    destination = config.getoption("--release-receipts")
    if destination:
        config.pluginmanager.register(ReleaseReceipts(Path(destination)), "release-receipt-writer")


def _sha(value):
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def safe_node_id(node_id):
    """Parameter values can contain fixture data; expose a stable opaque suffix."""
    base, separator, parameters = str(node_id).partition("[")
    if not re.fullmatch(r"[A-Za-z0-9_./:\\-]+", base):
        base = "node-sha256:" + _sha(base)
    return base + ("[sha256:" + _sha(parameters)[:16] + "]" if separator else "")


def reason_classification(outcome, when, reason):
    if outcome == "failed":
        return "test_failure" if when == "call" else "collection_or_fixture_error"
    text = reason.lower()
    classes = (
        ("non_host_platform", ("platforms(", "windows", "macos", "darwin", "linux only", "unsupported platform")),
        ("live_opt_in_required", ("opt-in", "opt in", "live test", "hermes_run_", "requires live", "e2e")),
        ("optional_dependency_unavailable", ("not installed", "no module", "could not import", "dependency")),
        ("environment_requirement_unavailable", ("not available", "unavailable", "not found", "requires", "not configured")),
        ("expected_failure", ("xfail", "expected failure")),
    )
    return next((label for label, markers in classes if any(marker in text for marker in markers)), "declared_skip")


class ReleaseReceipts:
    def __init__(self, destination):
        self.destination = Path(destination)
        self.counts = {"passed": 0, "failed": 0, "skipped": 0, "collection_errors": 0, "deselected": 0}
        self.exceptions = []

    def _retain(self, report, when):
        reason = str(report.longrepr)
        self.exceptions.append({"node_id": safe_node_id(report.nodeid), "node_id_sha256": _sha(str(report.nodeid)),
            "outcome": report.outcome, "phase": when,
            "classification": reason_classification(report.outcome, when, reason),
            "reason_sha256": _sha(reason), "expected_failure": bool(getattr(report, "wasxfail", False))})

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.outcome in {"failed", "skipped"}:
            self.counts[report.outcome] += 1
        if report.outcome in {"failed", "skipped"}:
            self._retain(report, report.when)

    def pytest_collectreport(self, report):
        if report.outcome == "failed":
            self.counts["collection_errors"] += 1
            self._retain(report, "collection")
        elif report.outcome == "skipped":
            self.counts["skipped"] += 1
            self._retain(report, "collection")

    def pytest_deselected(self, items):
        self.counts["deselected"] += len(items)

    def pytest_sessionfinish(self, session, exitstatus):
        receipt = {"schema_version": 1, "exit_code": int(exitstatus), "tests_collected": session.testscollected,
            "selection": [safe_node_id(str(value)) for value in session.config.args],
            "counts": self.counts, "exceptions": self.exceptions,
            "scope": "Observed pytest outcomes only; no outcome mutation; no raw parameter values or assertion payloads"}
        path = self.destination / (str(os.getpid()) + "-" + uuid.uuid4().hex + ".json")
        partial = path.with_suffix(".partial")
        try:
            self.destination.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
            partial.replace(path)
        except OSError as error:
            # Reporting storage is not test execution. A missing receipt blocks
            # release evidence, but must not replace pytest's true exit status.
            self.write_error = {"errno": error.errno, "exit_code_preserved": int(exitstatus)}
            try:
                if sys.__stderr__ is not None:
                    sys.__stderr__.write(f"BE18 receipt unavailable (errno={error.errno}); pytest exit status preserved\n")
            except OSError:
                # A full filesystem may also hold redirected stderr. The final
                # collector must detect the missing receipt independently.
                return
