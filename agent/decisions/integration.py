"""Opt-in core observers over real lifecycle and owner-bound source seams.

All current consumers are observation-only, even when an SDK caller configures
advisory/enforce. No prompts, tool schemas, approvals or monitor routes change.
Private packets currently produce privacy-blocked receipts until BE14 qualifies
storage/governance AND a destination-bound authorization path is implemented.
"""
from __future__ import annotations

import json
import time

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import ModelBundle, canonical, digest, require
from agent.decisions.policy import PointPolicy
from agent.decisions.receipts import JournalSink, scope_digest
from agent.decisions.registry import REGISTRY
from agent.decisions.state import build_state

_HOOKS = {"pre_api_request": "DP16", "pre_tool_call": "DP06", "transform_tool_result": "DP07"}


def parse_settings(raw):
    if raw in (None, {}):
        return None
    require(isinstance(raw, dict), "invalid_decision_config")
    version = raw.get("schema_version")
    require(type(version) is int and version in {1, 2}, "invalid_decision_config")
    allowed = ({"schema_version", "bundle", "points", "node", "tls"} if version == 1 else
               {"schema_version", "bundle", "points", "protocol", "destination"})
    require(set(raw) <= allowed, "invalid_decision_config")
    if version == 2:
        require(raw.get("protocol") == "laya_systemone", "unsupported_decision_protocol")
    points = raw.get("points", {})
    require(isinstance(points, dict) and set(points) <= set(REGISTRY), "unknown_point")
    policies = {}
    for point, record in points.items():
        require(isinstance(record, dict) and set(record) <= {"mode", "threshold", "timeout_seconds", "effect_thresholds"}, "invalid_decision_config")
        record = dict(record)
        if "effect_thresholds" in record:
            require(isinstance(record["effect_thresholds"], dict), "invalid_thresholds")
            record["effect_thresholds"] = tuple(sorted(record["effect_thresholds"].items()))
        policies[point] = PointPolicy(**record)
        # There are no enforcing production consumers in BE15. Config cannot
        # smuggle a self-approved point gate into the runtime.
        require(policies[point].mode != "enforce", "point_gate_required")
    if not any(policy.mode != "off" for policy in policies.values()):
        return None
    bundle = raw.get("bundle")
    require(isinstance(bundle, dict) and set(bundle) == {"model_digest", "calibration_digest", "service_digest"}, "invalid_bundle")
    resolved_bundle = ModelBundle(**bundle)
    if version == 2:
        require(all(point == "DP16" or policy.mode == "off" for point, policy in policies.items()),
                "laya_dp16_only")
        from agent.decisions.laya_transport import LayaDestinationManifest
        destination = LayaDestinationManifest.from_record(raw.get("destination"))
        require(all(getattr(destination, "expected_" + key) == getattr(resolved_bundle, key)
                    for key in ("model_digest", "calibration_digest", "service_digest")), "bundle_mismatch")
    return resolved_bundle, policies


def _settings():
    from hermes_cli.config import load_config_readonly
    return load_config_readonly().get("decisions", {})


def handles_hook(hook_name):
    point = _HOOKS.get(hook_name)
    if point is None:
        return False
    try:
        parsed = parse_settings(_settings())
        return parsed is not None and parsed[1].get(point, PointPolicy()).mode != "off"
    except Exception:
        return False


def _transport(raw):
    if raw.get("schema_version") == 2:
        from agent.decisions.laya_transport import LayaDestinationManifest, LayaHttpsTransport
        return LayaHttpsTransport(LayaDestinationManifest.from_record(raw.get("destination")),
                                 bundle=ModelBundle(**raw["bundle"]))
    node = raw.get("node")
    if node is None:
        return None
    from agent.decisions.transport import LanTransport, NodeManifest
    manifest = NodeManifest.from_record(node)
    tls = raw.get("tls")
    require(isinstance(tls, dict) and set(tls) == {"ca_file", "client_certificate", "client_key"}, "invalid_tls_config")
    require(all(isinstance(value, str) and value for value in tls.values()), "invalid_tls_config")
    require(manifest.registry_digest == digest({key: value.contract_digest for key, value in REGISTRY.items()}), "registry_digest_mismatch")
    for key in ("model_digest", "calibration_digest", "service_digest"):
        require(getattr(manifest, key) == raw["bundle"][key], "bundle_mismatch")
    return LanTransport(manifest, **tls)


def _client(run, raw, parsed):
    key = digest({"config": raw, "scope": scope_digest(run.context), "run": run.run_id})
    cached = getattr(run.agent, "_typed_decision_client", None)
    if cached is None or cached[0] != key:
        client = DecisionClient(bundle=parsed[0], policies=parsed[1], transport=_transport(raw), sink=JournalSink(run))
        run.agent._typed_decision_client = (key, client)
        return client
    return cached[1]


def observe_core(point_id, values, *, question_id=None):
    """Returns an inspectable outcome for hosts, never a control directive."""
    try:
        raw = _settings()
        parsed = parse_settings(raw)
        if parsed is None or parsed[1].get(point_id, PointPolicy()).mode == "off":
            return None
        from agent.runtime_commands import assert_runtime_dispatch
        from tools.capability_broker import require_live_policy
        context = require_live_policy()
        run = assert_runtime_dispatch()
        require(context is not None and context == run.context, "decision_owner_required")
        policy = parsed[1][point_id]
        from agent.decisions.planner_runtime import record_observer_policy
        record_observer_policy(run, point_id, policy)
        deadline = time.time() + policy.timeout_seconds
        if run.budget is not None:
            deadline = min(deadline, run.budget.deadline)
        packet = build_state(point_id, values, scope_digest=scope_digest(context))
        callback = lambda: _client(run, raw, parsed).decide(point_id, packet, 1, deadline, question_id=question_id)
        return _observe_with_budget(run, deadline, callback)
    except Exception:
        # An optional observer must not alter incumbents or print user payloads.
        return None


def _observe_with_budget(run, deadline, callback, *, completion_unknown=None):
    """Reserve actual observer wall time without poisoning the main retry controller."""
    if run.budget is None:
        return callback()
    import math
    import uuid
    budget = run.budget
    maximum = math.floor((deadline - time.time()) * 1000)
    require(maximum > 0, "deadline_exceeded")
    operation = "decision_" + uuid.uuid4().hex
    # Use the existing transactional ledger. Optional admission refusal must not
    # set BudgetRuntime.blocked_reason as a main-model request refusal would.
    budget.db.reserve_budget(budget.account_id, budget.actor, operation, {"wall_ms": maximum},
                             deadline=deadline, **budget.fence)
    started = time.monotonic()
    try:
        granted = budget.db.mark_budget_dispatched(budget.account_id, budget.actor, operation, **budget.fence)
        require(granted.get("dispatch_granted") is True, "decision_budget_denied")
    except Exception:
        budget.db.release_budget_reservation(budget.account_id, budget.actor, operation, **budget.fence)
        raise
    try:
        return callback()
    finally:
        unknown = bool(completion_unknown is not None and completion_unknown())
        budget.db.settle_budget(budget.account_id, budget.actor, operation,
            {"wall_ms": math.ceil((time.monotonic() - started) * 1000)},
            unknown_usage=unknown, slots_released=not unknown, **budget.fence)


def _compact(value):
    if isinstance(value, str):
        return value[:4096].replace("\0", "")
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return canonical(value)[:4096].replace("\0", "")
    return ""


def observe_lifecycle(hook_name, **kwargs):
    try:
        return _observe_lifecycle(hook_name, **kwargs)
    except Exception:
        return None


def _observe_lifecycle(hook_name, **kwargs):
    if hook_name == "pre_api_request":
        # Observe once at the front door, not on provider retries or tool rounds.
        if kwargs.get("retry_count", 0) or kwargs.get("api_call_count", 1) != 1:
            return None
        request = kwargs.get("request", {})
        body = request.get("body", {}) if isinstance(request, dict) else {}
        tools = body.get("tools", []) if isinstance(body, dict) else []
        from agent.runtime_commands import assert_runtime_dispatch
        from tools.capability_broker import require_live_policy
        from tools.agent_policy_gate import authorize_tool
        from agent.decisions.tool_planner import BRIDGES
        raw = _settings()
        parsed = parse_settings(raw)
        if parsed is None or parsed[1].get("DP16", PointPolicy()).mode == "off":
            return None
        context = require_live_policy()
        run = assert_runtime_dispatch()
        require(context is not None and context == run.context, "decision_owner_required")
        if any(authorize_tool(name, context=context) is not None for name in BRIDGES):
            # Do not strand a profile behind an ungranted escape path. Preserve
            # the incumbent need-only observer when planning cannot be applied.
            menu = [tool.get("function", {}).get("name", "") for tool in tools[:64]
                    if isinstance(tool, dict) and isinstance(tool.get("function", {}), dict)] if isinstance(tools, list) else []
            return observe_core("DP16", {"request": _compact(kwargs.get("user_message", "")),
                "menu": menu, "catalog_version": digest(menu)})
        from agent.decisions.planner_runtime import observe_front_door
        return observe_front_door(run, _client(run, raw, parsed),
            request_definitions=tools if isinstance(tools, list) else [],
            request=_compact(kwargs.get("user_message", "")))
    if hook_name == "pre_tool_call":
        return observe_core("DP06", {"tool_name": _compact(kwargs.get("tool_name", "")),
            "arguments": _compact(kwargs.get("args", {}))})
    if hook_name == "transform_tool_result":
        snippet = _compact(kwargs.get("result", ""))
        return observe_core("DP07", {"tool_name": _compact(kwargs.get("tool_name", "")),
            "snippet": snippet, "source_digest": digest(snippet)})
    return None
