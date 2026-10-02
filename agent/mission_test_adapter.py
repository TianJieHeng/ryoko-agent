"""One bounded, host-observed mission test adapter over BE05 isolated Python.

This is not a shell runner, model tool, or second agent loop. Exact declared code
runs against immutable BE07 input versions only after live BE03/BE05 admission.
The supervisor's wait status is proof; stdout and runtime log prose are not.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
import tempfile
import threading
import time
import uuid

from agent.mission_contract import MissionCriterion, criterion_digest
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor, read_project_artifact
from tools.capability_broker import CapabilityDenied, require_live_policy
from tools.environments.isolated_python import (
    IsolationTerminationUncertain, IsolationUnavailable, execute_isolated_python,
)
from tools.workspace_manifest import MAX_FILE_BYTES, MAX_TOTAL_BYTES, StagedWorkspace

ADAPTER = "isolated_python_v1"
ISOLATION_PROFILE = "linux-namespace-python-v1"
MAX_TESTS = 4
MAX_CODE_BYTES = 8192
MAX_WALL_MS = 5000
_WITNESSES = {}
_WITNESS_LOCK = threading.Lock()


class MissionTestUnsupported(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def validate_test_criterion(criterion):
    """Return a normalized exact computation contract, never infer a shell command."""
    raw = criterion.to_dict() if hasattr(criterion, "to_dict") else criterion
    item = MissionCriterion.from_dict(raw).to_dict()
    params = item["parameters"]
    if (item["kind"] != "test_execution" or set(params) != {"adapter", "code", "code_sha256"}
            or params.get("adapter") != ADAPTER):
        raise MissionTestUnsupported("test_adapter_unsupported")
    code = params["code"]
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > MAX_CODE_BYTES:
        raise MissionTestUnsupported("test_code_limit")
    if params["code_sha256"] != hashlib.sha256(code.encode()).hexdigest():
        raise MissionTestUnsupported("test_code_digest_mismatch")
    refs = item["artifact_refs"]
    if not refs or len({(ref["artifact_id"], ref["version"]) for ref in refs}) != len(refs):
        raise MissionTestUnsupported("test_inputs_required")
    return item


def test_inputs_digest(criterion):
    """Canonical digest of the exact code plus ordered immutable input layout."""
    item = validate_test_criterion(criterion)
    return _digest({"adapter": ADAPTER, "code_sha256": item["parameters"]["code_sha256"],
                    "artifact_refs": item["artifact_refs"], "input_layout_version": 1})


# A witness is minted only at the concrete host execution edge and consumed once
# by SessionDB. No RPC accepts it; a dict or model/runtime log cannot substitute.
def _witness(run, record):
    token = object()
    with _WITNESS_LOCK:
        if len(_WITNESSES) >= 128:
            raise CapabilityDenied("test_witness_capacity", "Outstanding test observation limit reached")
        _WITNESSES[token] = (run, _json(record))
    return token


def _consume_execution_witness(run, token):
    if type(token) is not object:
        raise CapabilityDenied("test_observation_required", "Only a live host execution observation may be recorded")
    with _WITNESS_LOCK:
        value = _WITNESSES.pop(token, None)
    if value is None or value[0] is not run:
        raise CapabilityDenied("test_observation_required", "Only a live host execution observation may be recorded")
    return json.loads(value[1])


def _mission_matches(run, mission, item):
    from agent.runtime_commands import assert_runtime_dispatch
    if assert_runtime_dispatch() is not run or require_live_policy() != run.context:
        raise CapabilityDenied("test_run_required", "A test requires its exact admitted runtime owner")
    if run.budget is None or run.budget.db is not run.db:
        raise CapabilityDenied("test_budget_required", "A test requires a live finite execution budget")
    actor, access = artifact_actor(run.context), project_access(run.context)
    account = run.db.get_budget_account(run.budget.account_id, actor)
    if account["run_id"] != run.run_id or run.budget.actor != actor:
        raise CapabilityDenied("test_budget_required", "A test requires its own admitted run budget")
    current = run.db.get_mission(run.session_id, actor, access=access)
    if (current is None or current["mission_id"] != mission["mission_id"]
            or current["revision"] != mission["revision"] or current["state"] not in {"ready", "working"}
            or current["project_id"] != mission["project_id"] or current["budget_ref"] != run.budget.root_id
            or not any(criterion_digest(value) == criterion_digest(item) for value in current["acceptance"])):
        raise CapabilityDenied("test_mission_changed", "Test inputs must belong to this live mission revision")
    if sum(value["kind"] == "test_execution" for value in current["acceptance"]) > MAX_TESTS:
        raise MissionTestUnsupported("test_count_limit")
    if current.get("deadline") is not None and current["deadline"] <= time.time():
        raise CapabilityDenied("test_deadline", "Mission deadline elapsed")
    return actor, access


def _stage(run, mission, item, root):
    source = root / "source"
    source.mkdir(mode=0o700)
    (source / "artifacts").mkdir(mode=0o700)
    paths, manifest, total = [], [], 0
    actor, access = artifact_actor(run.context), project_access(run.context)
    for index, ref in enumerate(item["artifact_refs"]):
        row = run.db.read_artifact_version(ref["artifact_id"], ref["version"], actor, access=access)
        if (row["project_id"] != mission["project_id"] or row["publication_state"] != "committed"
                or row["descriptor"]["sha256"] != ref["digest"] or row["head_version"] != ref["version"]
                or row["derived_validity"] != "current"):
            raise CapabilityDenied("test_input_changed", "Test requires current committed artifact inputs")
        data = read_project_artifact(run.context, run.db, mission["project_id"], ref["artifact_id"], ref["version"])
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES - 32768:
            raise MissionTestUnsupported("test_input_byte_limit")
        if hashlib.sha256(data).hexdigest() != ref["digest"]:
            raise CapabilityDenied("test_input_changed", "Test input bytes differ from their immutable digest")
        name = f"artifacts/{index:04d}.bin"
        (source / name).write_bytes(data)
        paths.append(name)
        manifest.append({**ref, "path": "/inputs/" + name})
    (source / "manifest.json").write_text(_json({"schema_version": 1, "artifacts": manifest}), encoding="utf-8")
    (source / "code.py").write_text(item["parameters"]["code"], encoding="utf-8")
    paths += ["manifest.json", "code.py"]
    return StagedWorkspace.create(root / "stage", parent_root=source, input_paths=tuple(paths),
                                  base_revision=test_inputs_digest(item))


def _observation(result):
    """Normalize only the trusted supervisor return, never an inner program log."""
    status = result.get("status")
    terminal = (result.get("isolated") is True and result.get("executed") is True
                and result.get("isolation_profile") == ISOLATION_PROFILE
                and type(result.get("exit_code")) is int
                and isinstance(result.get("stdout"), str) and isinstance(result.get("stderr"), str)
                and all(isinstance(result.get(key), str) and re.fullmatch("[0-9a-f]{64}", result[key])
                        for key in ("stdout_sha256", "stderr_sha256")))
    if status in {"cancelled", "timed_out"} and not terminal:
        return {"status": status, "exit_code": None, "stdout_sha256": None, "stderr_sha256": None,
                "stdout_truncated": False, "stderr_truncated": False, "output_sha256": None,
                "isolation_profile": None, "reason_code": "test_execution_interrupted"}
    if not terminal or status not in {"completed", "failed", "timed_out"}:
        raise IsolationUnavailable("invalid trusted executor observation")
    truncated = bool(result.get("stdout_truncated") or result.get("stderr_truncated"))
    if status == "completed" and (result["exit_code"] != 0 or truncated or result.get("output_error")):
        status = "failed"
    return {"status": status, "exit_code": result["exit_code"],
        "stdout_sha256": result["stdout_sha256"], "stderr_sha256": result["stderr_sha256"],
        "stdout_truncated": bool(result.get("stdout_truncated")),
        "stderr_truncated": bool(result.get("stderr_truncated")),
        "output_sha256": _digest(result.get("outputs", [])), "isolation_profile": ISOLATION_PROFILE,
        "reason_code": "test_output_incomplete" if truncated or result.get("output_error") else "host_wait_status"}


def execute_mission_test(run, mission, criterion):
    """Dispatch at most once for one criterion in this run, retaining exact proof."""
    from tools.capability_broker import ActionSpec, issue_capability, consume_capability, tool_action
    from tools.terminal_tool import _get_env_config
    from agent.runtime_commands import assert_runtime_dispatch, assert_runtime_finalization
    item = validate_test_criterion(criterion)
    actor, access = _mission_matches(run, mission, item)
    if _get_env_config()["env_type"] != "local":
        raise MissionTestUnsupported("test_executor_not_certified")
    previous = run.db.get_mission_test_execution(run.session_id, actor, mission_id=mission["mission_id"],
        criterion_digest=criterion_digest(item), run_id=run.run_id, access=access)
    if previous is not None:
        return previous
    key = "mission-test:" + _digest({"mission_id": mission["mission_id"], "criterion": criterion_digest(item)})
    if any(effect["intent_key"] == key for effect in run.db.list_effects(run.session_id, actor, run_id=run.run_id, limit=500)):
        raise CapabilityDenied("test_reconciliation_required", "A prior test dispatch has no retained proof; do not replay")
    budget = run.budget
    maximum_ms = min(MAX_WALL_MS, budget.policy.record["request_timeout_ms"],
        int((min(budget.deadline, mission.get("deadline") or math.inf) - time.time()) * 1000))
    if maximum_ms <= 2100:
        budget.block("insufficient bounded time to launch and terminate mission test")
    # Grant checks precede reservation and staging; the final ticket additionally
    # binds the actual immutable workspace manifest at the launch edge.
    base = tool_action("execute_code", {"code_sha256": item["parameters"]["code_sha256"]})
    from tools.capability_broker import _authorize_action
    _authorize_action(base)
    operation = budget.reserve({"executor_slots": 1, "wall_ms": maximum_ms})
    started, dispatched, unknown, effect = time.monotonic(), False, False, None
    stages = Path(run.context.profile_home) / "cache" / "mission-tests"
    temp = None
    try:
        stages.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = tempfile.TemporaryDirectory(prefix="test-", dir=stages)
        workspace = _stage(run, mission, item, Path(temp.name))
        args = {"code_sha256": item["parameters"]["code_sha256"], "inputs_digest": test_inputs_digest(item),
                "workspace_manifest_digest": workspace.manifest.digest, "wall_ms": maximum_ms}
        base = tool_action("execute_code", args)
        action = ActionSpec(base.name, args, base.operation_class, resource_roots=(workspace.root,),
                            contract_digest=base.contract_digest)
        capability = issue_capability(action)
        _mission_matches(run, mission, item)
        consume_capability(capability, action)
        effect = run.db.prepare_effect(run.session_id, actor, holder=run.holder, generation=run.generation,
            run_id=run.run_id, operation_id=operation, intent_key=key, operation_type="mission_test_execution",
            input_digest=test_inputs_digest(item), target_ref="mission-test:" + criterion_digest(item),
            policy_digest=run.context.policy.digest, policy_version=str(run.context.policy.policy_version),
            input_revision=workspace.manifest.digest, artifact_revision=test_inputs_digest(item),
            action_digest=action.digest, provider_idempotency="unsupported")
        budget.dispatched(operation)
        dispatched = True
        effect = run.db.dispatch_effect(effect["effect_id"], actor, holder=run.holder, generation=run.generation)
        if not effect["dispatched_now"]:
            raise CapabilityDenied("test_reconciliation_required", "Test intent already dispatched")
        assert_runtime_dispatch()
        _mission_matches(run, mission, item)
        remaining = maximum_ms / 1000 - (time.monotonic() - started)
        if remaining <= 2:
            raise IsolationUnavailable("test launch allowance exhausted")
        def cancelled():
            try:
                _mission_matches(run, mission, item)
                return False
            except (ValueError, PermissionError, InterruptedError):
                return True
        observed = _observation(execute_isolated_python(item["parameters"]["code"], workspace=workspace,
            timeout_seconds=min(3, remaining - 2), wall_seconds=remaining, is_cancelled=cancelled))
        workspace.verify_inputs()
        record = {"schema_version": 1, "receipt_id": uuid.uuid4().hex, "mission_id": mission["mission_id"],
            "mission_revision": mission["revision"], "session_id": run.session_id, "project_id": mission["project_id"],
            "criterion_id": item["criterion_id"], "criterion_digest": criterion_digest(item),
            "artifact_refs": item["artifact_refs"], "run_id": run.run_id, "operation_id": operation,
            "effect_id": effect["effect_id"], "code_sha256": item["parameters"]["code_sha256"],
            "inputs_digest": test_inputs_digest(item), "workspace_manifest_digest": workspace.manifest.digest,
            "budget_account_id": budget.account_id, "observed_at": time.time(), **observed}
        assert_runtime_finalization(run)
        run.db.record_effect_outcome(effect["effect_id"], actor, holder=run.holder, generation=run.generation,
            state="confirmed", receipt={"kind": "isolated_python_wait_status", "receipt_id": record["receipt_id"],
                                       "sha256": _digest(record), "observed_at": record["observed_at"]})
        witness = _witness(run, record)
        try:
            return run.db._record_mission_test_execution(run, witness)
        except BaseException:
            run.dispatch_blocked.set()
            raise
        finally:
            with _WITNESS_LOCK:
                _WITNESSES.pop(witness, None)
    except IsolationTerminationUncertain:
        unknown = True
        if effect is not None:
            run.db.record_effect_outcome(effect["effect_id"], actor, holder=run.holder, generation=run.generation,
                state="outcome_unknown", evidence={"kind": "adapter_exception", "reason": "termination_unacknowledged"})
        return {"criterion_id": item["criterion_id"], "status": "outcome_uncertain"}
    except IsolationUnavailable:
        if effect is not None and effect["state"] == "dispatched":
            run.db.record_effect_outcome(effect["effect_id"], actor, holder=run.holder, generation=run.generation,
                state="failed", evidence={"kind": "adapter_exception", "reason": "enforcement_unavailable"})
        return {"criterion_id": item["criterion_id"], "status": "unsupported"}
    except BaseException:
        # Once dispatch was admitted, an unexpected failure cannot imply a refund
        # or a successful test. The unresolved effect records the lost proof.
        if dispatched:
            unknown = True
            run.dispatch_blocked.set()
        raise
    finally:
        if not dispatched:
            budget.db.release_budget_reservation(budget.account_id, actor, operation, **budget.fence)
        else:
            budget.settle(operation, {"wall_ms": math.ceil((time.monotonic() - started) * 1000)},
                          unknown=unknown, slots_released=not unknown)
        if temp is not None and not unknown:
            temp.cleanup()


def execute_mission_tests(run, mission):
    """Bounded in-turn consumer; expected unsupported/blocked checks stay visible."""
    criteria = [item for item in mission["acceptance"] if item["kind"] == "test_execution"]
    if len(criteria) > MAX_TESTS:
        return [{"criterion_id": item["criterion_id"], "status": "unsupported", "reason_code": "test_count_limit"}
                for item in criteria]
    results = []
    for item in criteria:
        try:
            results.append(execute_mission_test(run, mission, item))
        except MissionTestUnsupported as exc:
            results.append({"criterion_id": item["criterion_id"], "status": "unsupported", "reason_code": exc.code})
        except (ValueError, PermissionError, InterruptedError) as exc:
            results.append({"criterion_id": item["criterion_id"], "status": "blocked",
                            "reason_code": getattr(exc, "code", "test_admission_blocked")})
        if run.dispatch_blocked.is_set():
            break
    return results
