"""Bounded deterministic mission checks over committed, authorized artifacts.

No model output, source annotation, legacy shell ledger, or successful transport
response is proof. Verification reads immutable bytes and their live dependency
state; receipt persistence and mission transitions belong to the existing writer.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field

from agent.project_context import project_access
from agent.result_artifacts import artifact_actor, read_project_artifact
from hermes_cli.artifact_store import _sections
from tools.capability_broker import require_live_policy

MAX_CRITERIA = 32
MAX_DEPENDENCIES = 64
MAX_BYTES = 32 * 1024 * 1024
_COMMON = {"require_current_head", "require_current_dependencies"}
_PARAMETERS = {
    "existence": _COMMON,
    "markdown_sections": _COMMON | {"required_sections", "nonempty"},
    "text_exact": _COMMON | {"contains", "excludes", "equals"},
    "json_schema": _COMMON | {"schema"},
    "linked_consistency": _COMMON | {"sections", "tokens"},
    "test_execution": {"command", "evidence_ref", "adapter", "code", "code_sha256"},
    "user_acceptance": set(),
}
_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const",
                "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"}
_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
          "boolean": bool, "null": type(None)}


class MissionVerificationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class _Blocked(Exception):
    def __init__(self, code):
        self.code = code


class _Unsupported(Exception):
    def __init__(self, code):
        self.code = code


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def evidence_dependency_digest(anchor):
    """Fingerprint retained evidence state without annotations or private text."""
    return _digest({key: anchor[key] for key in ("kind", "source_ref", "source_version", "range_ref",
        "captured_at", "authority", "validity", "effective_validity", "fresh_until")})


def dependency_is_current(db, conn, dependency, actor, access, *, criterion=None):
    """Recheck receipt bindings inside the authoritative state writer, without blob I/O."""
    try:
        if dependency["kind"] == "artifact":
            row = db._artifact_row_on_conn(conn, dependency["reference"], dependency["version"], actor, access)
            stored = db._artifact_result_on_conn(conn, row)
            if stored["descriptor"]["sha256"] != dependency["digest"]:
                return False
            criterion = criterion or {}
            root = any(ref["artifact_id"] == dependency["reference"]
                       and ref["version"] == dependency["version"] and ref["digest"] == dependency["digest"]
                       for ref in criterion.get("artifact_refs", []))
            params = criterion.get("parameters", {}) if root else {}
            return ((not params.get("require_current_head", True) or stored["head_version"] == stored["version"])
                    and (not params.get("require_current_dependencies", True) or stored["derived_validity"] == "current"))
        if dependency["kind"] == "evidence":
            row = db._source_row_on_conn(conn, "artifact_evidence_anchors", "anchor_id", dependency["reference"], actor, access)
            anchor = db._evidence_result_on_conn(conn, row)
            return (anchor["effective_validity"] == "current" and dependency["status"] == "current"
                    and anchor["source_version"] == dependency["version"]
                    and evidence_dependency_digest(anchor) == dependency["digest"])
    except (ValueError, PermissionError, KeyError, TypeError):
        return False
    return False


def _strings(value, *, required=False):
    if (not isinstance(value, list) or len(value) > 64 or (required and not value)
            or any(not isinstance(item, str) or not item or len(item) > 4096 for item in value)):
        raise _Unsupported("invalid_parameters")
    return value


def _parameters(criterion):
    kind, params = criterion["kind"], criterion["parameters"]
    if kind not in _PARAMETERS or not isinstance(params, dict) or set(params) - _PARAMETERS[kind]:
        raise _Unsupported("unsupported_criterion_parameters")
    for flag in _COMMON | {"nonempty"}:
        if flag in params and type(params[flag]) is not bool:
            raise _Unsupported("invalid_parameters")
    if len(_json(params)) > 32768:
        raise _Unsupported("parameters_limit")
    return params


def _deadline(value):
    now = time.time()
    value = now + 10 if value is None else value
    if type(value) not in (int, float) or value < 0 or value > now + 60 or not math.isfinite(value):
        raise MissionVerificationError("invalid_deadline", "Verification requires a finite deadline within sixty seconds")
    return float(value)


@dataclass
class _Observation:
    context: object
    db: object
    project_id: str
    deadline_at: float
    dependencies: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    byte_budget: list = field(default_factory=lambda: [0])
    visiting: set = field(default_factory=set)

    def check(self):
        if time.time() >= self.deadline_at:
            raise _Blocked("verification_deadline")
        if require_live_policy(require_run=False) != self.context:
            raise PermissionError("Verification requires its live requesting identity")

    @property
    def actor(self):
        return artifact_actor(self.context)

    @property
    def access(self):
        return project_access(self.context)

    def add_dependency(self, key, value):
        self.check()
        if key not in self.dependencies and len(self.dependencies) >= MAX_DEPENDENCIES:
            raise _Blocked("dependency_limit")
        self.dependencies[key] = value

    def artifact(self, reference, *, current=True, dependencies=True, depth=0):
        self.check()
        key = (reference["artifact_id"], reference["version"])
        if key in self.visiting or depth > 8:
            raise _Blocked("dependency_cycle_or_depth")
        if key in self.artifacts:
            row, data = self.artifacts[key]
            self._artifact_status(row, reference, current, dependencies)
            return row, data
        if len(self.artifacts) >= MAX_DEPENDENCIES:
            raise _Blocked("dependency_limit")
        row = self.db.read_artifact_version(*key, self.actor, access=self.access)
        if row["project_id"] != self.project_id or row["publication_state"] != "committed":
            raise _Blocked("artifact_scope_or_publication")
        descriptor = row["descriptor"]
        status = ("stale_derivative" if row["derived_validity"] != "current" else
                  "stale_head" if row["head_version"] != row["version"] else "current")
        self.add_dependency(("artifact", *key), {"kind": "artifact", "reference": key[0],
            "version": key[1], "digest": descriptor["sha256"], "status": status})
        self._artifact_status(row, reference, current, dependencies)
        if self.byte_budget[0] + descriptor["size"] > MAX_BYTES:
            raise _Blocked("artifact_byte_limit")
        data = read_project_artifact(self.context, self.db, self.project_id, *key)
        self.byte_budget[0] += len(data)
        self.check()
        self.artifacts[key] = (row, data)
        if dependencies:
            self.visiting.add(key)
            try:
                for ref in row["metadata"].get("derived_from", []):
                    self.artifact(ref, current=True, dependencies=True, depth=depth + 1)
                for anchor_id in row["metadata"].get("source_refs", []):
                    self.evidence(anchor_id, depth=depth + 1)
            finally:
                self.visiting.remove(key)
        return row, data

    @staticmethod
    def _artifact_status(row, reference, current, dependencies):
        if "digest" in reference and row["descriptor"]["sha256"] != reference["digest"]:
            raise _Blocked("artifact_digest_mismatch")
        if current and row["head_version"] != row["version"]:
            raise _Blocked("artifact_head_stale")
        if dependencies and row["derived_validity"] != "current":
            raise _Blocked("artifact_derivative_stale")

    def evidence(self, anchor_id, *, depth):
        self.check()
        key = ("evidence", anchor_id)
        if key in self.dependencies:
            return
        anchor = self.db.get_evidence_anchor(anchor_id, self.actor, access=self.access)
        if anchor["project_id"] != self.project_id:
            raise _Blocked("evidence_scope")
        self.add_dependency(key, {"kind": "evidence", "reference": anchor_id,
            "version": anchor["source_version"], "digest": evidence_dependency_digest(anchor), "status": anchor["effective_validity"]})
        if anchor["effective_validity"] != "current":
            raise _Blocked("evidence_not_current")
        ref = anchor["source_ref"]
        if "artifact_id" in ref:
            self.artifact(ref, depth=depth)
        elif "capture_id" in ref:
            capture = self.db.get_capture(ref["capture_id"], self.actor, access=self.access)
            if capture["project_id"] != self.project_id:
                raise _Blocked("capture_scope")
            self.artifact(capture["original_ref"], depth=depth)
        else:
            # Approval annotations cannot stand in for source or execution proof.
            raise _Blocked("source_approval_not_evidence")


def _text(data):
    if len(data) > 2 * 1024 * 1024 or data.count(b"\n") > 20000:
        raise _Blocked("artifact_text_limit")
    try:
        result = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Blocked("artifact_not_utf8") from exc
    if "\x00" in result:
        raise _Blocked("artifact_not_plain_text")
    return result


def _existence(artifacts, params):
    return bool(artifacts), ["committed_bytes_exist"] if artifacts else ["no_artifact_refs"]


def _markdown(artifacts, params):
    headings = _strings(params.get("required_sections"), required=True)
    outcomes = []
    for row, data in artifacts:
        if row["descriptor"]["mime"] != "text/markdown":
            return False, ["markdown_mime_required"]
        sections = _sections(_text(data))
        for heading in headings:
            section = sections.get(heading)
            if section is None:
                outcomes.append("required_section_missing_or_ambiguous")
            elif params.get("nonempty", True) and not section[2].partition("\n")[2].strip():
                outcomes.append("required_section_empty")
    return not outcomes, sorted(set(outcomes)) or ["required_sections_present"]


def _exact(artifacts, params):
    contains = _strings(params.get("contains", []))
    excludes = _strings(params.get("excludes", []))
    equals = params.get("equals")
    if (not contains and not excludes and equals is None) or (equals is not None and not isinstance(equals, str)):
        raise _Unsupported("invalid_parameters")
    failures = set()
    for _row, data in artifacts:
        text = _text(data)
        if any(value not in text for value in contains):
            failures.add("required_text_missing")
        if any(value in text for value in excludes):
            failures.add("excluded_text_present")
        if equals is not None and text != equals:
            failures.add("exact_text_mismatch")
    return not failures, sorted(failures) or ["exact_text_checks_passed"]


def _schema_definition(schema, *, depth=0, nodes=None):
    nodes = [0] if nodes is None else nodes
    nodes[0] += 1
    if depth > 8 or nodes[0] > 256:
        raise _Unsupported("schema_limit")
    if not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS:
        raise _Unsupported("unsupported_schema_keyword")
    if "type" in schema and (not isinstance(schema["type"], str) or schema["type"] not in _TYPES):
        raise _Unsupported("unsupported_schema_type")
    if "required" in schema:
        _strings(schema["required"])
    for name in ("minItems", "maxItems", "minLength", "maxLength"):
        if name in schema and (type(schema[name]) is not int or not 0 <= schema[name] <= 1000000):
            raise _Unsupported("invalid_schema_limit")
    for name in ("minimum", "maximum"):
        if name in schema and (type(schema[name]) not in (int, float)
                               or type(schema[name]) is float and not math.isfinite(schema[name])):
            raise _Unsupported("invalid_schema_limit")
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise _Unsupported("unsupported_schema_keyword")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not 1 <= len(schema["enum"]) <= 64):
        raise _Unsupported("invalid_schema_enum")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 64:
        raise _Unsupported("schema_limit")
    for key, value in properties.items():
        if not isinstance(key, str) or len(key) > 256:
            raise _Unsupported("invalid_schema_property")
        _schema_definition(value, depth=depth + 1, nodes=nodes)
    if "items" in schema:
        _schema_definition(schema["items"], depth=depth + 1, nodes=nodes)


def _schema_matches(value, schema, *, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 64 or count[0] > 4096:
        raise _Blocked("json_instance_limit")
    name = schema.get("type")
    if name is not None:
        allowed = _TYPES[name]
        if not isinstance(value, allowed) or (name in {"integer", "number"} and isinstance(value, bool)):
            return False
    if "enum" in schema and not any(_json(value) == _json(item) for item in schema["enum"]):
        return False
    if "const" in schema and _json(value) != _json(schema["const"]):
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and set(value) - properties.keys():
            return False
        return all(_schema_matches(item, properties.get(key, {}), depth=depth + 1, count=count)
                   for key, item in value.items())
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", MAX_BYTES):
            return False
        return all(_schema_matches(item, schema.get("items", {}), depth=depth + 1, count=count) for item in value)
    if isinstance(value, str):
        return schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_BYTES)
    if type(value) in (int, float):
        return (type(value) is int or math.isfinite(value)) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
    return True


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_schema(artifacts, params):
    schema = params.get("schema")
    _schema_definition(schema)
    for _row, data in artifacts:
        try:
            instance = json.loads(_text(data), object_pairs_hook=_unique_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (ValueError, RecursionError):
            return False, ["invalid_json"]
        if not _schema_matches(instance, schema):
            return False, ["schema_mismatch"]
    return True, ["local_schema_subset_passed"]


def _linked(artifacts, params):
    if len(artifacts) < 2:
        raise _Unsupported("linked_artifacts_required")
    texts = {row["artifact_id"]: _text(data) for row, data in artifacts}
    if len(texts) != len(artifacts):
        raise _Unsupported("ambiguous_linked_artifact_versions")
    tokens = _strings(params.get("tokens", []))
    sections = params.get("sections", [])
    if not isinstance(sections, list) or len(sections) > 64 or (not sections and not tokens):
        raise _Unsupported("invalid_parameters")
    bodies, failures = [], set()
    for item in sections:
        if (not isinstance(item, dict) or set(item) != {"artifact_id", "heading"}
                or not isinstance(item["artifact_id"], str) or item["artifact_id"] not in texts
                or not isinstance(item["heading"], str)):
            raise _Unsupported("invalid_linked_section")
        section = _sections(texts[item["artifact_id"]]).get(item["heading"])
        if section is None:
            failures.add("linked_section_missing_or_ambiguous")
        else:
            bodies.append(section[2].partition("\n")[2].strip())
    if sections and (len(sections) < 2 or len({item["artifact_id"] for item in sections}) < 2):
        raise _Unsupported("linked_artifacts_required")
    if bodies and (not all(bodies) or len(set(bodies)) > 1):
        failures.add("linked_sections_differ")
    if any(token not in text for text in texts.values() for token in tokens):
        failures.add("linked_token_missing")
    return not failures, sorted(failures) or ["linked_artifacts_consistent"]


def _test_criterion(criterion):
    from agent.mission_test_adapter import MissionTestUnsupported, validate_test_criterion
    try:
        return validate_test_criterion(criterion)
    except MissionTestUnsupported as exc:
        raise _Unsupported(exc.code) from exc


def _execution(observation, criterion):
    from agent.mission_contract import criterion_digest
    from agent.mission_test_adapter import ISOLATION_PROFILE, test_inputs_digest
    session_id = observation.context.identity.session_id
    mission = observation.db.get_mission(session_id, observation.actor, access=observation.access)
    expected = criterion_digest(criterion)
    if (mission is None or mission["project_id"] != observation.project_id
            or not any(criterion_digest(item) == expected for item in mission["acceptance"])):
        raise _Blocked("test_mission_binding_unavailable")
    record = observation.db.get_mission_test_execution(session_id, observation.actor,
        mission_id=mission["mission_id"], criterion_digest=expected, access=observation.access)
    if record is None:
        raise _Blocked("authenticated_execution_evidence_unavailable")
    if (record.get("mission_id") != mission["mission_id"] or record.get("project_id") != observation.project_id
            or record.get("criterion_id") != criterion["criterion_id"] or record.get("criterion_digest") != expected
            or record.get("artifact_refs") != criterion["artifact_refs"]
            or record.get("inputs_digest") != test_inputs_digest(criterion)
            or record.get("code_sha256") != criterion["parameters"]["code_sha256"]):
        raise _Blocked("test_execution_binding_mismatch")
    reference = "mission_test_execution:" + record["receipt_id"]
    if (record.get("isolation_profile") != ISOLATION_PROFILE or record.get("stdout_truncated")
            or record.get("stderr_truncated") or record.get("reason_code") == "test_output_incomplete"):
        return "blocked", ["test_execution_proof_incomplete"], [], reference
    if record["status"] == "completed" and type(record.get("exit_code")) is int and record["exit_code"] == 0:
        return "pass", [], ["authenticated_host_exit_zero"], reference
    if record["status"] == "failed" and type(record.get("exit_code")) is int and record["exit_code"] != 0:
        return "fail", [], ["authenticated_host_exit_nonzero"], reference
    return "blocked", ["test_execution_not_successful"], [], reference


_CHECKS = {"existence": _existence, "markdown_sections": _markdown, "text_exact": _exact,
           "json_schema": _json_schema, "linked_consistency": _linked}


def _normalize(criterion):
    from agent.mission_contract import MissionCriterion
    raw = criterion.to_dict() if hasattr(criterion, "to_dict") else criterion
    return MissionCriterion.from_dict(raw).to_dict()


def verify_criterion(context, db, project_id, criterion, *, deadline_at=None):
    """Observe one immutable criterion without persisting or executing anything."""
    return _verify_criterion(context, db, project_id, criterion, deadline_at=_deadline(deadline_at), byte_budget=[0])


def _verify_criterion(context, db, project_id, criterion, *, deadline_at, byte_budget):
    from agent.mission_contract import criterion_digest
    criterion = _normalize(criterion)
    observation = _Observation(context, db, project_id, deadline_at, byte_budget=byte_budget)
    result, reasons, checks = "blocked", [], []
    params = {}
    execution_reference = None
    try:
        observation.check()
        with observation.access.guard(project_id, observation.actor, "read"):
            params = _parameters(criterion)
            if criterion["kind"] == "test_execution":
                # Only the certified adapter's witnessed state record can prove
                # execution. Legacy ledger rows and stdout assertions cannot.
                _test_criterion(criterion)
            if criterion["kind"] == "user_acceptance":
                raise _Unsupported("user_acceptance_requires_separate_control")
            if not criterion["artifact_refs"]:
                raise _Blocked("no_artifact_refs")
            artifacts = [observation.artifact(ref, current=params.get("require_current_head", True),
                dependencies=params.get("require_current_dependencies", True)) for ref in criterion["artifact_refs"]]
            if criterion["kind"] == "test_execution":
                result, reasons, checks, execution_reference = _execution(observation, criterion)
            else:
                passed, checks = _CHECKS[criterion["kind"]](artifacts, params)
                result = "pass" if passed else "fail"
            observation.check()
    except _Unsupported as exc:
        result, reasons = "unsupported", [exc.code]
    except _Blocked as exc:
        reasons = [exc.code]
    except (OSError, ValueError, PermissionError):
        # No filesystem path, private snippet, or ungranted metadata in receipts.
        reasons = ["authorized_artifact_or_source_unavailable"]
    dependencies = sorted(observation.dependencies.values(), key=lambda row: _json(row))
    details = {"reason_codes": reasons, "checks": checks, "dependencies": dependencies,
               "inputs_digest": _digest({"artifact_refs": criterion["artifact_refs"], "dependencies": dependencies})}
    if len(_json(details).encode()) > 16000:
        result = "blocked"
        details["reason_codes"] = ["verification_evidence_limit"]
        details["checks"] = []
        while len(_json(details).encode()) > 16000:
            details["dependencies"].pop()
    body = {"criterion_id": criterion["criterion_id"], "criterion_digest": criterion_digest(criterion),
            "artifact_refs": criterion["artifact_refs"], "verifier": "deterministic_artifact_v1",
            "result": result, "details": details}
    return {**body, "evidence_ref": execution_reference or "sha256:" + _digest(body), "observed_at": time.time()}


def verify_mission(context, db, mission, *, deadline_at=None):
    """Return independent receipts; one failed criterion never erases passed work."""
    mission = mission.to_dict() if hasattr(mission, "to_dict") else mission
    if not isinstance(mission, dict) or not isinstance(mission.get("acceptance"), list):
        raise MissionVerificationError("invalid_mission", "Mission needs explicit acceptance criteria")
    criteria = mission["acceptance"]
    if not 1 <= len(criteria) <= MAX_CRITERIA:
        raise MissionVerificationError("criterion_limit", "Mission verification requires one to thirty-two criteria")
    deadline = _deadline(deadline_at)
    byte_budget = [0]
    return [_verify_criterion(context, db, mission["project_id"], criterion,
        deadline_at=deadline, byte_budget=byte_budget) for criterion in criteria]
