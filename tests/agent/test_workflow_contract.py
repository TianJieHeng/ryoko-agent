"""Executable workflow versions stay bounded, immutable and authority-free."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib

import pytest

from agent.workflow_contract import (
    TemplateVersion, WorkflowContractError, WorkflowVersion, canonical_json, validate_parameters,
)


def object_schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [],
            "additionalProperties": False}


def workflow_record():
    return {
        "workflow_id": "summary", "version": 1, "project_id": "project-a",
        "input_schema": object_schema({"name": {"type": "string"}}, ["name"]),
        "steps": [{"step_id": "greet", "kind": "render_markdown", "depends_on": [],
                   "parameters": {"template": "# Greeting\nHello ${input.name}\n"}}],
        "output_schema": {"required_sections": ["Greeting"], "min_bytes": 1},
        "capability_requirements": ["artifact_read", "artifact_write"],
        "environment_manifest": {"adapter": "local_deterministic_v1"},
        "provenance": {"kind": "manual", "source_refs": [], "private_derived": False},
        "predecessor": None, "template_ref": None,
    }


def test_canonical_content_and_nested_data_are_immutable():
    original = workflow_record()
    version = WorkflowVersion.from_record(original)
    digest = version.digest
    original["steps"][0]["parameters"]["template"] = "Changed"
    original["input_schema"]["properties"]["name"]["type"] = "integer"
    original["provenance"]["source_refs"].append({"artifact_id": "changed"})
    detached = version.to_record()
    detached["steps"].clear()
    detached["capability_requirements"].append("shell")
    assert version.to_record() == workflow_record()
    assert version.digest == digest
    assert digest == hashlib.sha256(canonical_json(version.to_record()).encode()).hexdigest()
    reversed_keys = dict(reversed(list(workflow_record().items())))
    assert WorkflowVersion.from_record(reversed_keys).digest == digest
    with pytest.raises(FrozenInstanceError):
        version.version = 2
    for malformed in ([], "not-json", "[" * 2000 + "]" * 2000):
        with pytest.raises(WorkflowContractError):
            replace(version, steps_json=malformed)
    successor = workflow_record()
    successor["version"] = 2
    successor["predecessor"] = {"workflow_id": version.workflow_id, "version": version.version, "sha256": digest}
    successor["steps"][0]["parameters"]["template"] += "Welcome!"
    assert WorkflowVersion.from_record(successor).digest != digest


def test_parameter_schema_recurses_and_rejects_undeclared_or_wrong_typed_values():
    schema = object_schema({
        "name": {"type": "string", "minLength": 1, "maxLength": 20},
        "age": {"type": "integer", "minimum": 0, "maximum": 150},
        "ratio": {"type": "number", "minimum": 0, "maximum": 1},
        "active": {"type": "boolean"},
        "details": object_schema({"city": {"type": "string"}}, ["city"]),
        "items": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2},
    }, ["name", "details"])
    data = {"name": "Ada", "age": 30, "ratio": .5, "active": True,
            "details": {"city": "Paris"}, "items": [1, 2]}
    assert validate_parameters(schema, data) is None
    for field, invalid in [("name", ""), ("name", "x" * 21), ("name", 2), ("age", True),
                           ("age", -1), ("age", 151), ("age", 2.5), ("ratio", False),
                           ("ratio", float("nan")), ("active", 1), ("details", {}),
                           ("details", {"city": "Paris", "extra": 1}), ("items", []),
                           ("items", [1, 2, 3]), ("items", [True])]:
        with pytest.raises(WorkflowContractError):
            validate_parameters(schema, {**data, field: invalid})
    with pytest.raises(WorkflowContractError):
        validate_parameters(schema, {**data, "unknown": "rejected"})
    with pytest.raises(WorkflowContractError):
        validate_parameters(schema, {"name": "Ada"})


@pytest.mark.parametrize("child", [
    {"type": "string", "pattern": ".*"}, {"$ref": "https://example.org/schema"},
    {"type": "null"}, {"type": ["string", "integer"]}, {"type": "object", "properties": {}},
    {"type": "object", "properties": {}, "additionalProperties": True},
    {"type": "object", "properties": {}, "required": ["unknown"], "additionalProperties": False},
    {"type": "array"}, {"type": "array", "items": {"type": "string"}, "maxItems": True},
    {"type": "number", "minimum": False}, {"type": "number", "minimum": 2, "maximum": 1},
    {"type": "string", "minLength": 2, "maxLength": 1},
])
def test_schema_never_accepts_unbounded_or_unimplemented_keywords(child):
    with pytest.raises(WorkflowContractError):
        validate_parameters(object_schema({"value": child}), {})


def test_dependency_graph_controls_input_and_prior_step_bindings():
    row = workflow_record()
    row["steps"] += [
        {"step_id": "repeat", "kind": "render_markdown", "depends_on": ["greet"],
         "parameters": {"template": "${steps.greet}\nAgain ${input.name}"}},
        {"step_id": "final", "kind": "render_markdown", "depends_on": ["repeat"],
         "parameters": {"template": "${steps.greet}\n${steps.repeat}"}},
    ]
    # Ordering is a DAG property, not an incidental position in the input array.
    row["steps"].reverse()
    version = WorkflowVersion.from_record(row)
    assert len(version.to_record()["steps"]) == 3
    no_dependency = deepcopy(row)
    no_dependency["steps"][0]["depends_on"] = []
    with pytest.raises(WorkflowContractError, match="dependency"):
        WorkflowVersion.from_record(no_dependency)
    cycle = deepcopy(row)
    cycle["steps"][-1]["depends_on"] = ["final"]
    with pytest.raises(WorkflowContractError, match="acyclic"):
        WorkflowVersion.from_record(cycle)
    missing = deepcopy(row)
    missing["steps"][0]["depends_on"] = ["missing"]
    with pytest.raises(WorkflowContractError, match="unknown"):
        WorkflowVersion.from_record(missing)


@pytest.mark.parametrize("template", ["${input.unknown}", "${steps.unknown}", "${input.name.upper()}",
                                       "${input.name.__class__}", "${__import__('os')}", "${input.name"])
def test_templates_only_accept_declared_simple_substitutions(template):
    row = workflow_record()
    row["steps"][0]["parameters"]["template"] = template
    with pytest.raises(WorkflowContractError):
        WorkflowVersion.from_record(row)


def test_domain_steps_use_installed_adapters_and_exact_recursive_bindings():
    from hermes_cli.domain_jobs import DOMAIN_ADAPTERS
    row = workflow_record()
    row["steps"].append({"step_id": "package", "kind": "domain", "depends_on": ["greet"],
                         "parameters": {"adapter": "decision", "arguments": {
                             "question": {"$input": "name"}, "nested": [{"prior": {"$step": "greet"}}]}}})
    assert WorkflowVersion.from_record(row).to_record()["steps"][-1]["parameters"]["adapter"] in DOMAIN_ADAPTERS
    for invalid in [{"$input": "name", "extra": True}, {"$step": "missing"}, {"$input": "missing"},
                    {"$input": ["name"]}, {"$input": "name", "$step": "greet"}]:
        changed = deepcopy(row)
        changed["steps"][-1]["parameters"]["arguments"]["question"] = invalid
        with pytest.raises(WorkflowContractError):
            WorkflowVersion.from_record(changed)
    for key, value in [("adapter", "shell"), ("arguments", {"context": {"$input": "name"}})]:
        changed = deepcopy(row)
        changed["steps"][-1]["parameters"][key] = value
        with pytest.raises(WorkflowContractError):
            WorkflowVersion.from_record(changed)


@pytest.mark.parametrize("field,value", [
    ("state", "approved"), ("evaluation_ref", "caller_says_passed"), ("version", True),
    ("version", 0), ("capability_requirements", ["shell"]), ("capability_requirements", ["browser"]),
    ("capability_requirements", ["artifact_read", "artifact_read"]),
    ("environment_manifest", {"adapter": "local_deterministic_v1", "host": "other-machine"}),
    ("environment_manifest", {"adapter": "live_browser"}),
    ("steps", []), ("output_schema", {"required_sections": [], "min_bytes": True}),
    ("output_schema", {"required_sections": [], "min_bytes": -1}),
    ("predecessor", {"workflow_id": "summary", "version": 1, "sha256": "0" * 64}),
    ("template_ref", {"template_id": "template", "version": 1, "sha256": "not-a-digest"}),
])
def test_versions_reject_lifecycle_authority_and_invalid_output_metadata(field, value):
    row = workflow_record()
    row[field] = value
    with pytest.raises(WorkflowContractError):
        WorkflowVersion.from_record(row)


def test_provenance_binds_immutable_evidence_without_granting_execution():
    row = workflow_record()
    provenance = {"kind": "accepted_mission", "reference": {
                      "session_id": "source-session", "mission_id": "accepted-1", "revision": 1},
                  "source_refs": [{"artifact_id": "source", "version": 1, "sha256": "a" * 64}],
                  "private_derived": True}
    row["provenance"] = provenance
    accepted = WorkflowVersion.from_record(row)
    provenance["private_derived"] = False
    with pytest.raises(WorkflowContractError, match="private_derived"):
        WorkflowVersion.from_record(row)
    provenance["private_derived"] = True
    provenance["reference"]["revision"] = True
    with pytest.raises(WorkflowContractError, match="version"):
        WorkflowVersion.from_record(row)
    provenance["reference"]["revision"] = 1
    del provenance["reference"]
    with pytest.raises(WorkflowContractError, match="reference"):
        WorkflowVersion.from_record(row)
    provenance["kind"] = "demonstration"
    with pytest.raises(WorkflowContractError, match="consent"):
        WorkflowVersion.from_record(row)
    provenance["consent_ref"] = {"artifact_id": "consent", "version": 1, "sha256": "b" * 64}
    assert WorkflowVersion.from_record(row).digest != accepted.digest
    row["steps"][0] = {"step_id": "click", "kind": "click", "parameters": {"x": 10, "y": 20}}
    with pytest.raises(WorkflowContractError, match="Unsupported workflow step"):
        WorkflowVersion.from_record(row)
    row["steps"] = workflow_record()["steps"]
    provenance["source_refs"][0].pop("sha256")
    with pytest.raises(WorkflowContractError, match="Reference"):
        WorkflowVersion.from_record(row)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), (1, 2), b"bytes",
                                   {1: "non-string key"}, "\ud800", "x" * (128 * 1024 + 1)])
def test_canonical_json_rejects_nonfinite_nonjson_and_oversized_data(value):
    with pytest.raises(WorkflowContractError):
        canonical_json(value)


def test_all_structure_and_step_count_limits_are_checked_before_execution():
    circular = []
    circular.append(circular)
    for invalid in (circular, [None] * 10001):
        with pytest.raises(WorkflowContractError, match="bound"):
            canonical_json(invalid)
    row = workflow_record()
    row["steps"] = [{**row["steps"][0], "step_id": f"step_{index}"} for index in range(17)]
    with pytest.raises(WorkflowContractError, match="steps"):
        WorkflowVersion.from_record(row)
    row["steps"] = [workflow_record()["steps"][0]] * 2
    with pytest.raises(WorkflowContractError, match="Duplicate"):
        WorkflowVersion.from_record(row)


def test_template_structure_is_immutable_independent_and_nonexecutable():
    record = {"template_id": "brief", "version": 1, "project_id": "project-a", "style": "Concise",
              "sections": ["Summary", "Next steps"], "predecessor": None}
    template = TemplateVersion.from_record(record)
    digest = template.digest
    record["sections"].append("Later")
    detached = template.to_record()
    detached["sections"].clear()
    assert template.to_record()["sections"] == ["Summary", "Next steps"]
    assert template.digest == digest
    for forbidden in ("steps", "capability_requirements", "environment_manifest", "state"):
        with pytest.raises(WorkflowContractError, match="template fields"):
            TemplateVersion.from_record({**template.to_record(), forbidden: []})
    successor = {**template.to_record(), "version": 2,
                 "predecessor": {"template_id": "brief", "version": 1, "sha256": digest}}
    assert TemplateVersion.from_record(successor).digest != digest
    row = workflow_record()
    row["template_ref"] = {"template_id": "brief", "version": 1, "sha256": digest}
    workflow = WorkflowVersion.from_record(row)
    successor["sections"].append("Appendix")
    TemplateVersion.from_record(successor)
    assert workflow.to_record()["template_ref"]["sha256"] == digest
