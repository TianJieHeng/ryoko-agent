"""Read-only coding review packages and fail-closed browser evidence projection.

The only execution proof accepted here is BE09's witnessed isolated-Python
receipt. Repository/worktree identities remain declarations: this adapter never
opens a host checkout, runs Git, promotes files, or releases anything. Browser
records are projected for reconciliation, not upgraded into certified actions.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import math
import re
import time

from agent.mission_contract import criterion_digest
from agent.mission_test_adapter import MAX_TESTS, validate_test_criterion
from agent.mission_verifier import verify_criterion
from agent.project_context import authorize_project, project_access
from agent.result_artifacts import artifact_actor
from hermes_cli.domain_media import _read
from tools.capability_broker import CapabilityDenied, require_live_policy
from tools.workspace_manifest import checked_path

MAX_CHANGES = 16
MAX_SOURCE_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_PAGE_AGE_SECONDS = 120


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _text(value, field, maximum=256):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{field} requires bounded nonempty text")
    return value


def _digest(value, field):
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} requires a SHA-256 digest")
    return value


def _scope(value):
    if not isinstance(value, dict) or set(value) != {"repository_id", "worktree_id", "baseline_sha"}:
        raise ValueError("Exact repository/worktree/baseline identity required")
    for field in ("repository_id", "worktree_id"):
        _text(value[field], field)
    if not isinstance(value["baseline_sha"], str) or re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", value["baseline_sha"]) is None:
        raise ValueError("Full immutable baseline SHA required")
    return dict(value)


def _key(ref):
    return ref["artifact_id"], ref["version"], ref["sha256"]


def _source(context, db, project_id, ref, *, current):
    raw, _mime = _read(context, db, project_id, ref)
    if len(raw) > MAX_SOURCE_BYTES or raw.count(b"\n") > 2000:
        raise ValueError("Coding source exceeds bounded review size")
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise ValueError("Coding source requires UTF-8 text without NUL")
    row = db.read_artifact_version(ref["artifact_id"], ref["version"], artifact_actor(context),
                                   access=project_access(context))
    if row["publication_state"] != "committed":
        raise ValueError("Coding source must be committed")
    if current and (row["head_version"] != ref["version"] or row["derived_validity"] != "current"):
        raise ValueError("Candidate source is stale")
    return text


def _syntax(source, path):
    try:
        tree = ast.parse(source, filename=path)
        if sum(1 for _ in ast.walk(tree)) > 20000:
            raise ValueError("Coding syntax tree exceeds bound")
        return {"status": "passed", "validator": "python_ast_parse"}
    except (SyntaxError, RecursionError) as exc:
        return {"status": "failed", "validator": "python_ast_parse", "line": getattr(exc, "lineno", None)}


def _changes(context, db, project_id, changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        raise ValueError("Coding review requires one to sixteen changes")
    result, refs, seen = [], {}, set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "before", "after"}:
            raise ValueError("Each change requires exact path/before/after fields")
        path = checked_path(change["path"])
        if path in seen or not path.endswith(".py") or len(path) > 256:
            raise ValueError("Only unique bounded Python source paths are supported")
        seen.add(path)
        before = "" if change["before"] is None else _source(context, db, project_id, change["before"], current=False)
        after = _source(context, db, project_id, change["after"], current=True)
        if before == after:
            raise ValueError("Changed-file review requires changed bytes")
        for ref in (change["before"], change["after"]):
            if ref is not None:
                refs[_key(ref)] = dict(ref)
        # Keep complete candidate bytes alongside the diff, including final-newline
        # differences which line-oriented unified diff renderers can obscure.
        diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile="baseline/" + path, tofile="candidate/" + path))
        result.append({"path": path, "before": change["before"], "after": change["after"],
            "change": "add" if change["before"] is None else "modify", "diff": diff,
            "candidate_source": after, "before_final_newline": before.endswith("\n"),
            "after_final_newline": after.endswith("\n"), "syntax": _syntax(after, path)})
    return result, refs


def _test_records(context, db, project_id, mission, test_ids, sources):
    if (not isinstance(test_ids, list) or not 1 <= len(test_ids) <= MAX_TESTS
            or any(not isinstance(value, str) for value in test_ids) or len(set(test_ids)) != len(test_ids)):
        raise ValueError("One to four unique mission test IDs required")
    actor, access = artifact_actor(context), project_access(context)
    acceptance = {item["criterion_id"]: item for item in mission["acceptance"]}
    records, covered = [], set()
    for test_id in test_ids:
        if test_id not in acceptance:
            raise ValueError("Test ID is not in this mission")
        item = validate_test_criterion(acceptance[test_id])
        input_keys = {(ref["artifact_id"], ref["version"], ref["digest"]) for ref in item["artifact_refs"]}
        if not input_keys <= sources.keys():
            raise ValueError("Test inputs extend beyond declared coding source artifacts")
        verification = verify_criterion(context, db, project_id, item)
        observed = db.get_mission_test_execution(context.identity.session_id, actor,
            mission_id=mission["mission_id"], criterion_digest=criterion_digest(item), access=access)
        authenticated = False
        if observed is not None and verification["result"] == "pass":
            # Reuse the completion writer's authoritative receipt/effect/budget
            # checks. Read only: no new evidence ledger or caller receipt seam.
            with db._mission_guard(context.identity.session_id, actor, access, "read"):
                with db._runtime_read() as conn:
                    authenticated = db._mission_test_receipt_current_on_conn(
                        conn, mission, item, verification, actor, access)
        if verification["result"] == "pass" and not authenticated:
            verification = {**verification, "result": "blocked", "details": {
                **verification["details"], "reason_codes": ["authenticated_execution_proof_not_current"]}}
        if authenticated:
            covered.update(input_keys)
        records.append({"criterion": item, "verification": verification,
                        "execution_receipt": observed, "authenticated": authenticated})
    return records, covered


def _package(kind, lines, metadata):
    markdown = ("\n".join(lines) + "\n").encode()
    record = (_json(metadata) + "\n").encode()
    if len(markdown) + len(record) > MAX_PACKAGE_BYTES:
        raise ValueError("Complete execution package exceeds byte bound")
    return {"content_bytes": markdown, "mime": "text/markdown", "metadata": metadata,
            "outputs": [{"name": kind + "-review.md", "mime": "text/markdown", "content_bytes": markdown},
                        {"name": kind + "-review.json", "mime": "application/json", "content_bytes": record}]}


def _fence(text):
    return "`" * max(3, 1 + max((len(item) for item in re.findall(r"`+", text)), default=0))


def build_coding_package(context, db, *, project_id, scope, changes, mission_id, mission_revision, test_ids):
    """Reopen exact source bytes and BE09 receipts into a complete review artifact.

    This is a read-only evidence consumer. To execute tests, use the existing
    admitted mission runtime before preparing the package. No caller pass/fail,
    release-approved flag, shell command, or receipt payload is accepted.
    """
    if require_live_policy(require_run=False) != context:
        raise PermissionError("Coding package requires its live identity")
    authorize_project(context, project_id, "read")
    scope = _scope(scope)
    if type(mission_revision) is not int or mission_revision <= 0:
        raise ValueError("Exact mission revision required")
    _text(mission_id, "mission_id")
    actor, access = artifact_actor(context), project_access(context)
    mission = db.get_mission(context.identity.session_id, actor, access=access)
    if (mission is None or mission["mission_id"] != mission_id or mission["revision"] != mission_revision
            or mission["project_id"] != project_id):
        raise ValueError("Coding package mission identity or revision differs")
    reviewed, sources = _changes(context, db, project_id, changes)
    tests, covered = _test_records(context, db, project_id, mission, test_ids, sources)
    candidates = {_key(change["after"]) for change in reviewed}
    tested = (candidates <= covered and all(test["authenticated"] for test in tests)
              and all(change["syntax"]["status"] == "passed" for change in reviewed))
    current = db.get_mission(context.identity.session_id, actor, access=access)
    if current["revision"] != mission_revision or current["mission_id"] != mission_id:
        raise ValueError("Mission changed during coding review")
    metadata = {"schema_version": 1, "kind": "coding", "inputs": list(sources.values()),
        "scope": scope, "scope_authority": "operator_declaration_not_checkout_attestation",
        "mission_id": mission_id, "mission_revision": mission_revision, "changes": reviewed, "tests": tests,
        "targeted_validation": "passed" if tested else "blocked", "coverage": "exact_input_binding_not_semantic_coverage",
        "review_gate": {"state": "pending", "reason": "human_changed_file_review_required"},
        "release_gate": {"state": "blocked", "reason_codes": ["repository_baseline_not_host_attested", "changed_file_review_pending"]
                         + ([] if tested else ["targeted_validation_incomplete"])},
        "permissions": {"artifact_publication": "separate_exact_approval", "repository_publication": "unsupported",
                        "merge": "unsupported", "deploy": "unsupported", "file_promotion": "unsupported"},
        "unsupported": ["host_git", "host_terminal", "dependency_installation", "existing_file_promotion", "multi_file_promotion"],
        "transformations": ["immutable_source_reopen", "bounded_python_syntax_parse", "changed_file_diff",
                            "be09_authenticated_receipt_recheck", "read_only_review_package"],
        "validator_manifest": {"source_bytes": "sha256_verified", "targeted_tests": "passed" if tested else "blocked",
                               "test_adapter": "isolated_python_v1", "release": "blocked", "external_effects": "none"}}
    lines = ["# Coding review package", "", f"Repository: {scope['repository_id']}", f"Worktree: {scope['worktree_id']}",
        f"Baseline SHA: {scope['baseline_sha']}", "", "Repository/worktree/baseline are operator declarations, not a host checkout attestation.",
        "", "## Changed files"]
    for change in reviewed:
        fence = _fence(change["diff"] + change["candidate_source"])
        lines += [f"### {change['path']}", f"Change: {change['change']}; candidate SHA-256: {change['after']['sha256']}",
                  "", fence + "diff", change["diff"], fence, "", "Complete candidate source:", fence + "python", change["candidate_source"], fence]
    lines += ["", "## Targeted test evidence"]
    for test in tests:
        receipt = test["execution_receipt"]
        lines += [f"- {test['criterion']['criterion_id']}: {test['verification']['result']}; authenticated: {test['authenticated']}",
                  f"  Receipt: {receipt['receipt_id'] if receipt else 'unavailable'}; code SHA-256: {test['criterion']['parameters']['code_sha256']}"]
    lines += ["", "## Release gate", f"Targeted validation: {metadata['targeted_validation']}.",
              "Release blocked: human changed-file review and authenticated repository baseline are still required.",
              "Artifact publication requires separate exact approval. Repository publication, merge, deploy and file promotion are unsupported.",
              "Only BE09 isolated stdlib Python execution is recognized. Host Git, host terminal and dependency installation are unsupported.",
              "The JSON companion retains complete criteria, immutable source references, execution receipts and verification bindings."]
    return _package("coding", lines, metadata)


def require_browser_certification():
    """No F25 browser effect adapter is certified in this bounded production slice."""
    raise CapabilityDenied("browser_adapter_not_certified", "Browser dispatch/completion requires a certified current-page/effect adapter")


def build_browser_package(context, db, *, project_id, effect_id, page_ref, current_page_sha256,
                          input_sha256, target_ref, observed_at, now=None):
    """Project retained effect state; never execute, retry, or certify a browser action.

    Page digest and observation time are supplied comparison values, not trusted
    browser observations. Even exact, fresh bindings cannot pass certification.
    The fixed age ceiling cannot be enlarged by a request argument.
    """
    if require_live_policy(require_run=False) != context:
        raise PermissionError("Browser projection requires its live identity")
    authorize_project(context, project_id, "read")
    _digest(current_page_sha256, "current page digest")
    _digest(input_sha256, "input digest")
    _text(target_ref, "target_ref", 1024)
    now = time.time() if now is None else now
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in (now, observed_at)):
        raise ValueError("Finite page observation and comparison timestamps required")
    raw, _mime = _read(context, db, project_id, page_ref)
    effect = db.get_effect(effect_id, artifact_actor(context))
    if effect["session_id"] != context.identity.session_id or effect["operation_type"] != "browser_submit":
        raise ValueError("Effect is not this session's browser submit intent")
    age = now - observed_at
    checks = {"page_bytes": hashlib.sha256(raw).hexdigest() == current_page_sha256 == effect["input_revision"],
              "input_digest": input_sha256 == effect["input_digest"], "target": target_ref == effect["target_ref"],
              "age": 0 <= age <= MAX_PAGE_AGE_SECONDS}
    metadata = {"schema_version": 1, "kind": "browser", "inputs": [page_ref], "effect_id": effect_id,
        "effect_state": effect["state"], "effect_receipts": effect["evidence"], "binding_checks": checks,
        "observed_at": observed_at, "comparison_at": now, "age_seconds": age,
        "observation_authority": "supplied_comparison_not_live_browser_attestation",
        "completion": "unverified", "dispatch_permitted": False, "replay_permitted": False,
        "certification": "unsupported", "reason_code": "browser_adapter_not_certified",
        "transformations": ["immutable_page_reopen", "exact_page_input_target_age_comparison", "authoritative_effect_projection"],
        "validator_manifest": {"bindings": "matched" if all(checks.values()) else "mismatched",
            "current_page": "not_attested", "browser_completion": "unsupported", "external_effects": "none"}}
    lines = ["# Browser evidence review", "", f"Retained effect: {effect_id}", f"Authoritative state: {effect['state']}",
             "", "## Evidence comparisons", *[f"- {key}: {'matched' if value else 'mismatched'}" for key, value in checks.items()],
             "", "## Certification gate", "Blocked: no certified browser action adapter is configured.",
             "Supplied page/time values cannot attest the live browser. Payload text is not proof of a completed action.",
             "An outcome_unknown submit stays unknown until authoritative read-only reconciliation; never resubmit it here.",
             "Authentication, payment and high-impact actions require their separate policy handoffs."]
    return _package("browser", lines, metadata)
