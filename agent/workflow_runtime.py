"""Finite manual workflow interpreter. Procedure text never becomes tool authority.

Only deterministic local artifact producers are installed. Evaluations use this
same interpreter without publishing, so A/B comparisons cannot replay mutations.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager

from agent.artifact_commands import ArtifactControlRun, assert_artifact_dispatch
from agent.project_context import authorize_project, project_access
from agent.result_artifacts import artifact_actor
from hermes_state_workflows import WorkflowRegistry, canonical, digest, require

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_INTERPOLATION = re.compile(r"\$\{(input|steps)\.([A-Za-z_][A-Za-z0-9_-]*)\}")


def assert_workflow_control(run, methods):
    """Promotion/publication cannot be reached from a model's runtime scope."""
    from tui_gateway import server
    require(isinstance(run, ArtifactControlRun) and server._current_rpc_method.get() in methods,
            "workflow_human_control_required", "Exact owned human workflow control required")
    return assert_artifact_dispatch(run)


def verify_provenance(run, definition):
    from hermes_cli.domain_media import _read
    provenance = definition["provenance"]
    for ref in provenance["source_refs"]:
        _read(run.context, run.db, definition["project_id"], ref)
    if provenance["kind"] == "accepted_mission":
        ref = provenance["reference"]
        # Exact session/mission/revision is parsed, never guessed from a title.
        require(isinstance(ref, dict) and set(ref) == {"session_id", "mission_id", "revision"},
                "workflow_provenance_invalid", "Accepted mission requires exact session, mission and revision")
        mission = run.db.get_mission(ref["session_id"], artifact_actor(run.context), access=project_access(run.context))
        require(mission is not None and mission["mission_id"] == ref["mission_id"]
                and mission["revision"] == ref["revision"] and mission["project_id"] == definition["project_id"]
                and mission["acceptance_status"] == "accepted" and mission["verification_current"],
                "workflow_mission_unaccepted", "Only exact human-accepted, currently verified work is reusable evidence")
    if provenance["kind"] == "demonstration":
        # Consent is a retained artifact, not a bare recording URL or authority.
        consent = provenance["consent_ref"]
        require(isinstance(consent, dict), "workflow_consent_required", "Demonstration requires a scoped consent artifact")
        raw, mime = _read(run.context, run.db, definition["project_id"], consent)
        require(mime == "application/json", "workflow_consent_required", "Consent must be a structured retained artifact")
        record = json.loads(raw)
        require(set(record) == {"purpose", "project_id", "source_refs", "retention_until"}
                and record["purpose"] == "workflow_extraction" and record["project_id"] == definition["project_id"]
                and record["source_refs"] == provenance["source_refs"]
                and type(record["retention_until"]) in (int, float) and record["retention_until"] > time.time(),
                "workflow_consent_required", "Demonstration consent is expired or names different recordings")


def _resolve(value, parameters, outputs):
    if isinstance(value, dict):
        if set(value) == {"$input"}:
            require(value["$input"] in parameters, "workflow_binding_missing", "Required parameter is missing")
            return parameters[value["$input"]]
        if set(value) == {"$step"}:
            require(value["$step"] in outputs, "workflow_binding_missing", "Prior step output is missing")
            return outputs[value["$step"]]
        return {key: _resolve(child, parameters, outputs) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve(child, parameters, outputs) for child in value]
    return value


def _render(template, parameters, outputs, *, maximum=MAX_OUTPUT_BYTES):
    chunks, size, offset = [], 0, 0
    def append(value):
        nonlocal size
        data = value.encode("utf-8")
        size += len(data)
        require(size <= maximum, "workflow_output_bound", "Interpolated output exceeds its byte budget")
        chunks.append(data)
    for match in _INTERPOLATION.finditer(template):
        append(template[offset:match.start()])
        source = parameters if match[1] == "input" else outputs
        require(match[2] in source, "workflow_binding_missing", "Required template binding is missing")
        value = source[match[2]]
        require(type(value) in (str, int, float, bool), "workflow_binding_invalid", "Markdown bindings must be scalar")
        append(str(value))
        offset = match.end()
    append(template[offset:])
    return b"".join(chunks)


@contextmanager
def _bounded_local(run):
    # The synchronous adapter is finite by input/output bounds. Actual elapsed
    # wall is charged; an overrun remains ledger debt, never refunded or hidden.
    budget = run.budget
    operation, started = None, time.monotonic()
    if budget is not None:
        ceiling = min(budget.policy.record["request_timeout_ms"], max(1, int((budget.deadline - time.time()) * 1000)))
        operation = budget.reserve({"executor_slots": 1, "wall_ms": ceiling})
        budget.dispatched(operation)
    try:
        yield
    finally:
        if operation is not None:
            budget.settle(operation, {"wall_ms": max(0, int((time.monotonic() - started) * 1000))})
    assert_artifact_dispatch(run)


def execute_workflow(run, definition, parameters, *, admitted_at):
    from agent.workflow_contract import validate_parameters
    from hermes_cli.domain_jobs import DomainJob, build_domain_package
    from hermes_cli.artifact_formats import validate_artifact
    assert_artifact_dispatch(run)
    record = definition.to_record()
    validate_parameters(record["input_schema"], parameters)
    authorize_project(run.context, definition.project_id, "write")
    registry = WorkflowRegistry(run.context, run.db)
    if record["template_ref"]:
        registry.template(definition.project_id, record["template_ref"])
    for capability in record["capability_requirements"]:
        authorize_project(run.context, definition.project_id, {"artifact_read": "read", "artifact_write": "write"}[capability])
    pending, values, outputs, sources, total = list(record["steps"]), {}, [], [], 0
    with _bounded_local(run):
        while pending:
            step = next((item for item in pending if set(item["depends_on"]) <= set(values)), None)
            require(step is not None, "workflow_dependency_invalid", "Workflow dependencies are not ready")
            assert_artifact_dispatch(run)
            if step["kind"] == "render_markdown":
                produced = [{"name": step["step_id"] + ".md", "mime": "text/markdown",
                             "content_bytes": _render(step["parameters"]["template"], parameters, values, maximum=MAX_OUTPUT_BYTES - total)}]
            else:
                arguments = _resolve(step["parameters"]["arguments"], parameters, values)
                canonical(arguments)  # bound expanded bindings before adapter serialization
                job = DomainJob.from_record({"job_id": "workflow-" + step["step_id"], "project_id": definition.project_id,
                    "adapter": step["parameters"]["adapter"], "arguments": arguments})
                package = build_domain_package(run.context, run.db, job, admitted_at=admitted_at)
                produced = package.get("outputs") or [{"name": step["step_id"] + ".md", "mime": package.get("mime", "text/markdown"),
                                                       "content_bytes": package["content_bytes"]}]
                sources.extend(package["metadata"].get("inputs", []))
            for item in produced:
                total += len(item["content_bytes"])
                require(total <= MAX_OUTPUT_BYTES and len(outputs) < 32, "workflow_output_bound", "Workflow output exceeds bound")
                validate_artifact(item["content_bytes"], item["mime"])
                outputs.append({**item, "step_id": step["step_id"]})
            primary = produced[0]
            require(primary["mime"] in {"text/markdown", "text/plain", "text/csv", "application/json"},
                    "workflow_binding_invalid", "A step's primary output must be text or JSON")
            text = primary["content_bytes"].decode("utf-8")
            values[step["step_id"]] = json.loads(text) if primary["mime"] == "application/json" else text
            pending.remove(step)
    final = outputs[-len(produced)]["content_bytes"]
    from hermes_cli.artifact_store import _sections
    expectation = record["output_schema"]
    require(len(final) >= expectation["min_bytes"], "workflow_output_invalid", "Final output is shorter than its declared expectation")
    if expectation["required_sections"]:
        require(primary["mime"] == "text/markdown", "workflow_output_invalid", "Section expectations require Markdown")
        sections = _sections(final.decode())
        require(all(sections.get(section) is not None for section in expectation["required_sections"]),
                "workflow_output_invalid", "Final output is missing an unambiguous required section")
    return {"outputs": outputs, "final_sha256": hashlib.sha256(final).hexdigest(), "source_refs": sources,
            "output_sha256": digest([{"step_id": item["step_id"], "name": item["name"], "mime": item["mime"],
                                      "sha256": hashlib.sha256(item["content_bytes"]).hexdigest()} for item in outputs])}


def evaluate_workflow(run, row, cases):
    from agent.workflow_contract import WorkflowVersion, validate_parameters
    from hermes_cli.domain_media import _read
    assert_workflow_control(run, {"runtime.workflow.evaluate"})
    require(isinstance(cases, list) and 4 <= len(cases) <= 16, "workflow_evaluation_invalid", "At least two varied tuning and two held-out cases required")
    registry = WorkflowRegistry(run.context, run.db)
    definition = WorkflowVersion.from_record(json.loads(row["definition_json"]))
    predecessor = definition.to_record()["predecessor"]
    incumbent = None
    if predecessor:
        old = registry.get(row["project_id"], row["workflow_id"], predecessor["version"])
        require(old["sha256"] == predecessor["sha256"], "workflow_predecessor_mismatch", "Incumbent digest changed")
        incumbent = WorkflowVersion.from_record(json.loads(old["definition_json"]))
    observed, seen, ids = [], set(), set()
    admitted_at = run.db.read_runtime_run_accepted_at(run.session_id, run.run_id)
    for case in cases:
        require(isinstance(case, dict) and set(case) == {"case_id", "split", "parameters", "expected_sha256", "generalist_ref"},
                "workflow_evaluation_invalid", "Exact case inputs, labels, split and recorded generalist output are required")
        require(isinstance(case["case_id"], str) and 0 < len(case["case_id"]) <= 128 and case["case_id"] not in ids,
                "workflow_evaluation_invalid", "Case identities must be unique and bounded")
        ids.add(case["case_id"])
        require(case["split"] in {"tuning", "held_out"} and isinstance(case["expected_sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", case["expected_sha256"]) is not None,
                "workflow_evaluation_invalid", "Case split and exact expected digest required")
        validate_parameters(definition.to_record()["input_schema"], case["parameters"])
        input_digest = digest(case["parameters"])
        require(input_digest not in seen, "workflow_evaluation_invalid", "Evaluation must use varied nonduplicate inputs")
        seen.add(input_digest)
        baseline, _mime = _read(run.context, run.db, row["project_id"], case["generalist_ref"])
        started = time.monotonic()
        produced = execute_workflow(run, definition, case["parameters"], admitted_at=admitted_at)
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        old_digest = None
        if incumbent is not None:
            try:
                old_digest = execute_workflow(run, incumbent, case["parameters"], admitted_at=admitted_at)["final_sha256"]
            except ValueError:
                old_digest = None
        observed.append({"case_id": case["case_id"], "split": case["split"], "input_sha256": input_digest,
            "expected_sha256": case["expected_sha256"], "actual_sha256": produced["final_sha256"],
            "passed": produced["final_sha256"] == case["expected_sha256"], "elapsed_ms": latency_ms,
            "generalist_ref": case["generalist_ref"], "generalist_passed": hashlib.sha256(baseline).hexdigest() == case["expected_sha256"],
            "incumbent_sha256": old_digest, "incumbent_passed": old_digest == case["expected_sha256"] if incumbent else None})
    require(all(sum(case["split"] == split for case in observed) >= 2 for split in ("tuning", "held_out")),
            "workflow_evaluation_invalid", "Separate varied tuning and held-out cases required")
    evidence = {"evaluation_id": "workflow-eval-" + uuid.uuid4().hex, "workflow_sha256": row["sha256"],
        "cases": observed, "case_manifest_sha256": digest(cases), "cases_json": canonical(cases),
        "passed": all(case["passed"] for case in observed), "adapter": "local_deterministic_v1",
        "baseline_authority": "recorded_human_supplied_generalist_outputs_not_live_attestation",
        "label_authority": "human_supplied_expected_digests", "incumbent": predecessor,
        "external_effects": "none", "training_performed": False, "observed_at": time.time(),
        "candidate_passes": sum(case["passed"] for case in observed),
        "generalist_passes": sum(case["generalist_passed"] for case in observed),
        "incumbent_passes": sum(case["incumbent_passed"] is True for case in observed) if incumbent else None}
    return {"workflow": registry.save_evaluation(run, row, evidence), "evaluation_json": canonical(evidence)}
