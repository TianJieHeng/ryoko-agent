"""Finite, immutable BE11 executable contracts, separate from review lifecycle.

Versions contain data for installed local adapters, never executable Python,
shell, browser actions, or grants. JSON-backed fields and detached record copies
keep callers from changing the bytes covered by a version's ``digest`` property.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

MAX_CONTRACT_BYTES = 128 * 1024
MAX_STEPS = 16
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 10000
ALLOWED_CAPABILITIES = frozenset({"artifact_read", "artifact_write"})
LOCAL_ENVIRONMENT = {"adapter": "local_deterministic_v1"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_BINDING_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
_PLACEHOLDER = re.compile(r"\$\{(input|steps)\.([A-Za-z_][A-Za-z0-9_-]{0,63})\}")


class WorkflowContractError(ValueError):
    """A workflow or template is outside the finite contract."""


def _require(condition, message):
    if not condition:
        raise WorkflowContractError(message)


def canonical_json(value):
    """Return bounded, deterministic finite JSON; reject non-JSON Python values."""
    count, string_bytes = 0, 0

    def check(item, depth=0):
        nonlocal count, string_bytes
        count += 1
        _require(depth <= MAX_JSON_DEPTH and count <= MAX_JSON_NODES,
                 "Workflow JSON structure exceeds bound")
        if type(item) is dict:
            _require(all(type(key) is str for key in item), "JSON keys must be strings")
            for key, child in item.items():
                check(key, depth + 1)
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif type(item) is str:
            _require(len(item) <= MAX_CONTRACT_BYTES, "Workflow JSON text exceeds bound")
            string_bytes += len(item.encode("utf-8"))
            _require(string_bytes <= MAX_CONTRACT_BYTES, "Workflow JSON text bytes exceed bound")
        else:
            _require(item is None or type(item) in (bool, int, float), "Expected finite JSON")
            if type(item) is float:
                _require(math.isfinite(item), "Expected finite JSON numbers")

    try:
        check(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False)
        _require(len(result.encode("utf-8")) <= MAX_CONTRACT_BYTES, "Workflow JSON bytes exceed bound")
        return result
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, WorkflowContractError):
            raise
        raise WorkflowContractError("Expected bounded finite UTF-8 JSON") from exc


def _text(value, label, maximum=4096, *, empty=False):
    _require(type(value) is str and (empty or bool(value.strip()))
             and len(value.encode("utf-8")) <= maximum, f"Invalid bounded {label}")


def _identifier(value, label="identifier", *, binding=False):
    pattern = _BINDING_NAME if binding else _IDENTIFIER
    _require(type(value) is str and pattern.fullmatch(value) is not None, f"Invalid {label}")


def _version(value):
    _require(type(value) is int and 1 <= value < 2**31, "Expected positive version")


def _list(value, label, maximum=64):
    _require(type(value) is list and len(value) <= maximum, f"Invalid bounded {label}")
    return value


def _reference(value, identity):
    _require(type(value) is dict and set(value) == {identity, "version", "sha256"},
             "Reference requires exact identity, version and sha256")
    _identifier(value[identity], identity)
    _version(value["version"])
    _require(type(value["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None,
             "Reference requires a SHA-256 digest")


def _decode(value):
    _require(type(value) is str, "Immutable contract fields require JSON strings")
    try:
        return json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise WorkflowContractError("Invalid immutable contract JSON") from exc


_SCHEMA_KEYS = {
    "string": {"type", "description", "minLength", "maxLength"},
    "integer": {"type", "description", "minimum", "maximum"},
    "number": {"type", "description", "minimum", "maximum"},
    "boolean": {"type", "description"},
    "object": {"type", "description", "properties", "required", "additionalProperties"},
    "array": {"type", "description", "items", "minItems", "maxItems"},
}


def _schema(schema, depth=0):
    _require(depth <= 8 and type(schema) is dict, "Parameter schema exceeds bound")
    kind = schema.get("type")
    _require(type(kind) is str and kind in _SCHEMA_KEYS, "Unsupported parameter type")
    _require(set(schema) <= _SCHEMA_KEYS[kind], "Unsupported parameter schema keyword")
    if "description" in schema:
        _text(schema["description"], "schema description", empty=True)
    if kind == "object":
        properties = schema.get("properties")
        _require(type(properties) is dict and len(properties) <= 64, "Bounded object properties required")
        _require(schema.get("additionalProperties") is False, "Object schemas require additionalProperties false")
        required = _list(schema.get("required", []), "required properties")
        for name in required:
            _identifier(name, "required property", binding=True)
        _require(len(set(required)) == len(required) and set(required) <= properties.keys(),
                 "Required properties must be unique and declared")
        for name, child in properties.items():
            _identifier(name, "property name", binding=True)
            _schema(child, depth + 1)
    if kind == "array":
        _schema(schema.get("items"), depth + 1)
    bounds = {"string": ("minLength", "maxLength", 65536),
              "array": ("minItems", "maxItems", 1000)}
    if kind in bounds:
        lower, upper, ceiling = bounds[kind]
        for key in (lower, upper):
            if key in schema:
                _require(type(schema[key]) is int and 0 <= schema[key] <= ceiling,
                         f"Invalid {key} bound")
        _require(schema.get(lower, 0) <= schema.get(upper, ceiling), "Conflicting parameter bounds")
    if kind in {"integer", "number"}:
        for key in ("minimum", "maximum"):
            if key in schema:
                _require(type(schema[key]) in (int, float), "Numeric schema bounds must be numbers")
                if type(schema[key]) is float:
                    _require(math.isfinite(schema[key]), "Numeric schema bounds must be finite")
        if "minimum" in schema and "maximum" in schema:
            _require(schema["minimum"] <= schema["maximum"], "Conflicting numeric bounds")


def _value(schema, data, path):
    kind = schema["type"]
    matches = {"string": type(data) is str, "integer": type(data) is int,
               "number": type(data) in (int, float), "boolean": type(data) is bool,
               "object": type(data) is dict, "array": type(data) is list}
    _require(matches[kind], f"Parameter {path} must be {kind}")
    if kind == "object":
        _require(set(schema.get("required", [])) <= data.keys(), f"Parameter {path} is missing required properties")
        _require(data.keys() <= schema["properties"].keys(), f"Parameter {path} has undeclared properties")
        for key, child in data.items():
            _value(schema["properties"][key], child, f"{path}.{key}")
    if kind == "array":
        _require(schema.get("minItems", 0) <= len(data) <= schema.get("maxItems", 1000),
                 f"Parameter {path} exceeds array bounds")
        for index, child in enumerate(data):
            _value(schema["items"], child, f"{path}[{index}]")
    if kind == "string":
        _require(schema.get("minLength", 0) <= len(data) <= schema.get("maxLength", 65536),
                 f"Parameter {path} exceeds string bounds")
    if kind in {"integer", "number"}:
        _require(("minimum" not in schema or data >= schema["minimum"])
                 and ("maximum" not in schema or data <= schema["maximum"]),
                 f"Parameter {path} exceeds numeric bounds")


def validate_parameters(schema, data):
    """Validate the finite schema and input value, returning None on success."""
    canonical_json(schema)
    canonical_json(data)
    _schema(schema)
    _require(schema["type"] == "object", "Workflow inputs require an object schema")
    _value(schema, data, "input")


def _binding(value, inputs, step_refs):
    if type(value) is dict:
        reserved = set(value) & {"$input", "$step"}
        if reserved:
            _require(len(value) == 1, "Bindings must be exact single-key objects")
            key = next(iter(reserved))
            name = value[key]
            _identifier(name, "binding name", binding=True)
            if key == "$input":
                _require(name in inputs, "Binding names an undeclared input")
            else:
                step_refs.add(name)
        else:
            for child in value.values():
                _binding(child, inputs, step_refs)
    elif type(value) is list:
        for child in value:
            _binding(child, inputs, step_refs)


def _step(step, inputs):
    _require(type(step) is dict and set(step) <= {"step_id", "kind", "depends_on", "parameters"}
             and {"step_id", "kind", "parameters"} <= set(step), "Invalid workflow step fields")
    _identifier(step["step_id"], "step ID", binding=True)
    dependencies = _list(step.setdefault("depends_on", []), "step dependencies", MAX_STEPS)
    for dependency in dependencies:
        _identifier(dependency, "step dependency", binding=True)
    _require(len(set(dependencies)) == len(dependencies), "Duplicate step dependency")
    parameters, refs = step["parameters"], set()
    _require(type(parameters) is dict, "Step parameters must be an object")
    if step["kind"] == "render_markdown":
        _require(set(parameters) == {"template"}, "Markdown step requires only a template")
        template = parameters["template"]
        _text(template, "markdown template", 65536)
        for match in _PLACEHOLDER.finditer(template):
            scope, name = match.groups()
            if scope == "input":
                _require(name in inputs, "Template names an undeclared input")
            else:
                refs.add(name)
        _require("${" not in _PLACEHOLDER.sub("", template), "Unsupported markdown substitution")
    elif step["kind"] == "domain":
        from hermes_cli.domain_jobs import DOMAIN_ADAPTERS
        _require(set(parameters) == {"adapter", "arguments"}, "Domain step requires adapter and arguments")
        _require(type(parameters["adapter"]) is str and parameters["adapter"] in DOMAIN_ADAPTERS,
                 "Unsupported domain adapter; no live effects are available")
        arguments = parameters["arguments"]
        _require(type(arguments) is dict and len(arguments) <= 32, "Bounded domain arguments required")
        _require(not set(arguments) & {"project_id", "context", "db", "now"},
                 "Domain arguments cannot replace bound authority or clock")
        _binding(arguments, inputs, refs)
    else:
        raise WorkflowContractError("Unsupported workflow step kind; no executable code or live effects")
    return refs


def _steps(steps, inputs):
    _list(steps, "workflow steps", MAX_STEPS)
    _require(bool(steps), "At least one workflow step is required")
    by_id, references = {}, {}
    for step in steps:
        refs = _step(step, inputs)
        step_id = step["step_id"]
        _require(step_id not in by_id, "Duplicate workflow step ID")
        by_id[step_id], references[step_id] = step, refs
    visiting, ancestors = set(), {}

    def visit(step_id):
        _require(step_id in by_id, "Dependency names an unknown step")
        _require(step_id not in visiting, "Workflow dependencies must be acyclic")
        if step_id in ancestors:
            return ancestors[step_id]
        visiting.add(step_id)
        parents = set()
        for dependency in by_id[step_id]["depends_on"]:
            parents.add(dependency)
            parents.update(visit(dependency))
        visiting.remove(step_id)
        _require(references[step_id] <= parents, "Step bindings require a declared dependency path")
        ancestors[step_id] = parents
        return parents

    for step_id in by_id:
        visit(step_id)


def _provenance(value):
    _require(type(value) is dict and set(value) <= {"kind", "reference", "source_refs", "private_derived", "consent_ref"}
             and {"kind", "source_refs", "private_derived"} <= set(value), "Invalid workflow provenance fields")
    _require(type(value["kind"]) is str and value["kind"] in {"manual", "accepted_mission", "demonstration"},
             "Unknown workflow provenance kind")
    _require(type(value["private_derived"]) is bool, "Provenance private_derived must be boolean")
    _require(value["kind"] != "accepted_mission" or bool(value.get("reference")),
             "Accepted mission provenance requires a reference; runtime must verify acceptance")
    _require(value["kind"] != "demonstration" or bool(value.get("consent_ref")),
             "Demonstration provenance requires explicit consent evidence")
    if "reference" in value:
        if value["kind"] == "accepted_mission":
            reference = value["reference"]
            _require(type(reference) is dict and set(reference) == {"session_id", "mission_id", "revision"},
                     "Accepted mission reference requires exact session_id, mission_id and revision")
            _identifier(reference["session_id"], "source session ID")
            _identifier(reference["mission_id"], "source mission ID")
            _version(reference["revision"])
        else:
            _text(value["reference"], "provenance reference", 1024)
    if "consent_ref" in value:
        _reference(value["consent_ref"], "artifact_id")
    _require(value["private_derived"] or (value["kind"] == "manual" and not value["source_refs"]),
             "Source-derived procedures must remain private_derived")
    seen = set()
    for ref in _list(value["source_refs"], "source references"):
        _reference(ref, "artifact_id")
        key = (ref["artifact_id"], ref["version"], ref["sha256"])
        _require(key not in seen, "Duplicate source reference")
        seen.add(key)


def _workflow_record(record):
    row = json.loads(canonical_json(record))
    required = {"workflow_id", "version", "project_id", "input_schema", "steps", "output_schema",
                "capability_requirements", "environment_manifest", "provenance"}
    _require(type(row) is dict and required <= set(row) <= required | {"predecessor", "template_ref"},
             "Invalid workflow fields; lifecycle state is stored separately")
    _identifier(row["workflow_id"], "workflow ID")
    _identifier(row["project_id"], "project ID")
    _version(row["version"])
    _schema(row["input_schema"])
    _require(row["input_schema"]["type"] == "object", "Workflow inputs require an object schema")
    _steps(row["steps"], row["input_schema"]["properties"])
    output = row["output_schema"]
    _require(type(output) is dict and set(output) == {"required_sections", "min_bytes"},
             "Output contract requires required_sections and min_bytes")
    sections = _list(output["required_sections"], "required output sections", 32)
    for section in sections:
        _text(section, "required section", 256)
    _require(len(set(sections)) == len(sections), "Duplicate required section")
    _require(type(output["min_bytes"]) is int and 0 <= output["min_bytes"] <= 4 * 1024 * 1024,
             "Output min_bytes exceeds bound")
    capabilities = _list(row["capability_requirements"], "capabilities", len(ALLOWED_CAPABILITIES))
    _require(all(type(item) is str for item in capabilities), "Capability names must be strings")
    _require(len(set(capabilities)) == len(capabilities) and set(capabilities) <= ALLOWED_CAPABILITIES,
             "Unsupported capability; workflows cannot request host, shell, browser or live effects")
    _require(row["environment_manifest"] == LOCAL_ENVIRONMENT, "Only local_deterministic_v1 is supported")
    _provenance(row["provenance"])
    for field, identity in (("predecessor", "workflow_id"), ("template_ref", "template_id")):
        ref = row.setdefault(field, None)
        if ref is not None:
            if field == "template_ref" and ref.get("store") == "artifact_templates":
                _reference({key: value for key, value in ref.items() if key != "store"}, identity)
                _require(len(row["steps"]) == 1 and row["steps"][0]["kind"] == "render_markdown"
                         and row["steps"][0]["parameters"] == {"template": "__canonical_template__"},
                         "Canonical template workflows require one explicit __canonical_template__ render step")
            else:
                _reference(ref, identity)
    if row["predecessor"] is not None:
        _require(row["predecessor"]["workflow_id"] == row["workflow_id"]
                 and row["predecessor"]["version"] < row["version"], "Predecessor must be an earlier version of this workflow")
    return row


@dataclass(frozen=True)
class WorkflowVersion:
    workflow_id: str
    version: int
    project_id: str
    input_schema_json: str
    steps_json: str
    output_schema_json: str
    capability_requirements_json: str
    environment_manifest_json: str
    provenance_json: str
    predecessor_json: str = "null"
    template_ref_json: str = "null"

    def __post_init__(self):
        _workflow_record(self.to_record())

    @classmethod
    def from_record(cls, record):
        row = _workflow_record(record)
        return cls(**{key if key in {"workflow_id", "version", "project_id"} else key + "_json":
                      value if key in {"workflow_id", "version", "project_id"} else canonical_json(value)
                      for key, value in row.items()})

    def to_record(self):
        return {key[:-5] if key.endswith("_json") else key:
                _decode(getattr(self, key)) if key.endswith("_json") else getattr(self, key)
                for key in self.__dataclass_fields__}

    @property
    def digest(self):
        """SHA-256 of canonical executable content, excluding mutable lifecycle."""
        return hashlib.sha256(canonical_json(self.to_record()).encode("utf-8")).hexdigest()


def _template_record(record):
    row = json.loads(canonical_json(record))
    required = {"template_id", "version", "project_id", "style", "sections"}
    _require(type(row) is dict and required <= set(row) <= required | {"predecessor"},
             "Invalid template fields; templates cannot contain executable logic or capabilities")
    _identifier(row["template_id"], "template ID")
    _identifier(row["project_id"], "project ID")
    _version(row["version"])
    _text(row["style"], "template style", 4096, empty=True)
    sections = _list(row["sections"], "template sections", 32)
    for section in sections:
        _text(section, "template section", 256)
    _require(len(set(sections)) == len(sections), "Duplicate template section")
    predecessor = row.setdefault("predecessor", None)
    if predecessor is not None:
        _reference(predecessor, "template_id")
        _require(predecessor["template_id"] == row["template_id"] and predecessor["version"] < row["version"],
                 "Predecessor must be an earlier version of this template")
    return row


@dataclass(frozen=True)
class TemplateVersion:
    template_id: str
    version: int
    project_id: str
    style: str
    sections_json: str
    predecessor_json: str = "null"

    def __post_init__(self):
        _template_record(self.to_record())

    @classmethod
    def from_record(cls, record):
        row = _template_record(record)
        return cls(row["template_id"], row["version"], row["project_id"], row["style"],
                   canonical_json(row["sections"]), canonical_json(row["predecessor"]))

    def to_record(self):
        return {"template_id": self.template_id, "version": self.version, "project_id": self.project_id,
                "style": self.style, "sections": _decode(self.sections_json),
                "predecessor": _decode(self.predecessor_json)}

    @property
    def digest(self):
        """SHA-256 of canonical style/structure, independent of workflow versions."""
        return hashlib.sha256(canonical_json(self.to_record()).encode("utf-8")).hexdigest()
