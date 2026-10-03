"""Real owned DP16 observer/recovery seams; shipped default-off and non-mutating."""
from __future__ import annotations

import json
import time

from agent.decisions.contracts import digest, require
from agent.decisions.receipts import scope_digest
from agent.decisions.tool_planner import ToolPlanner, live_catalog


class PolicyJournal:
    """The owner/run fence and closed wire schema are rechecked for every write."""
    durable = True

    def __init__(self, run):
        self.run = run

    def __call__(self, record):
        from agent.runtime_commands import assert_runtime_dispatch
        from tools.capability_broker import require_live_policy
        from tui_gateway.contracts.decision_plans import DecisionPolicyRecord, DecisionPlannerMiss
        run = assert_runtime_dispatch()
        require(run is self.run and require_live_policy() == run.context, "receipt_authority_mismatch")
        scope = scope_digest(run.context)
        require(record.get("scope_digest", scope) == scope, "receipt_scope_mismatch")
        kind = record.get("kind")
        models = {"point_policy": ("decision.policy", DecisionPolicyRecord),
                  "planner_miss": ("decision.planner_miss", DecisionPlannerMiss)}
        require(kind in models, "unknown_decision_record")
        event, model = models[kind]
        payload = model.model_validate({**record, "scope_digest": scope}).model_dump(mode="json")
        return run.db.append_runtime_event(run.session_id, event, payload,
            holder=run.holder, generation=run.generation, run_id=run.run_id)


def record_observer_policy(run, point_id, policy):
    """Record effective observer configuration once per point/run/config tuple."""
    from agent.decisions.point_policies import PointRule
    scope = scope_digest(run.context)
    rule = PointRule(point_id, policy.mode, (("default", policy.threshold),) + policy.effect_thresholds, policy.timeout_seconds,
                     rollout_scope=(scope,))
    key = (run.run_id, point_id, rule.policy_digest, scope)
    recorded = getattr(run.agent, "_decision_observer_policy_keys", set())
    if key not in recorded:
        from dataclasses import asdict
        PolicyJournal(run)({"kind": "point_policy", "operation": "observer", **asdict(rule),
                            "policy_digest": rule.policy_digest})
        # Bound in-memory bookkeeping; durable history is in the governed journal.
        run.agent._decision_observer_policy_keys = set(list(recorded)[-31:]) | {key}
    return rule


def _authorized_definitions(run, request_definitions):
    """Read metadata only from the owner's existing discoverable snapshot.

    This performs no rediscovery/probe/provider call and never mutates the frozen
    prompt/tool view. Current policy is checked again by live_catalog below.
    """
    from tools.registry import registry
    view = getattr(run.agent, "tool_view", None)
    definitions = {item["function"]["name"]: item for item in request_definitions
                   if isinstance(item, dict) and isinstance(item.get("function"), dict)
                   and isinstance(item["function"].get("name"), str)}
    if view is not None:
        require(view.session_policy_version == run.context.policy.digest, "planner_owner_mismatch")
        discoverable = set(view.discoverable_tool_ids)
        for metadata in registry.catalog_metadata():
            if metadata.name in discoverable and metadata.name not in definitions:
                definitions[metadata.name] = {"type": "function", "function": json.loads(metadata.schema_json)}
    return list(definitions.values())


def observe_front_door(run, client, *, request_definitions, request, goal=""):
    """Invoked from the real pre_api_request seam, once per front-door message."""
    from tui_gateway.contracts.decision_plans import DecisionToolPlan
    policy = client.policy("DP16")
    if policy.mode == "off":
        return None
    require(policy.mode != "enforce", "owner_consumer_not_qualified")
    record_observer_policy(run, "DP16", policy)
    scope = scope_digest(run.context)
    deadline = time.time() + policy.timeout_seconds
    if run.budget is not None:
        deadline = min(deadline, run.budget.deadline)
    planner = ToolPlanner(client, catalog_lookup=lambda: live_catalog(
        _authorized_definitions(run, request_definitions), scope_digest=scope))
    plan = planner.plan(request=request, goal=goal, scope_digest=scope, deadline=deadline)
    payload = DecisionToolPlan.model_validate(plan.to_record()).model_dump(mode="json")
    run.db.append_runtime_event(run.session_id, "decision.tool_plan", payload,
        holder=run.holder, generation=run.generation, run_id=run.run_id)
    run.agent._decision_tool_plan = (run.run_id, plan, digest(request_definitions))
    return plan


def observe_bridge_recovery(requested_names, described_names, *, current_tool_defs):
    """Called after the existing authorized tool_describe owner resolves names.

    Shadow observations are candidate omissions, not real planner misses. The
    result continues through the existing bridge without toolset/schema edits.
    """
    try:
        from agent.runtime_commands import assert_runtime_dispatch
        run = assert_runtime_dispatch()
        saved = getattr(run.agent, "_decision_tool_plan", None)
        if saved is None or saved[0] != run.run_id:
            return
        _, plan, prefix_digest = saved
        if plan.fallback is not None or plan.need not in {"no_tools", "needs_tools"}:
            return
        scope = scope_digest(run.context)
        current = live_catalog(current_tool_defs, scope_digest=scope)
        require(plan.scope_digest == scope, "planner_owner_mismatch")
        live = set(current.tool_ids)
        described = set(described_names)
        for name in set(requested_names) - set(plan.verified_tool_ids):
            recovered = name in described and name in live
            # Names are digested, so rejected or stale names never become an
            # oracle for another agent's catalog, including the personal MCP.
            PolicyJournal(run)({"kind": "planner_miss", "scope_digest": scope,
                "bundle_id": plan.bundle_id, "catalog_version": current.version,
                "previous_catalog_version": plan.live_catalog_version, "tool_digest": digest(name),
                "recovered": recovered, "reason": "authorized_reopen" if recovered else "not_authorized_or_unavailable",
                "prefix_digest": prefix_digest, "observation_only": plan.mode != "enforce"})
    except Exception:
        # Optional diagnostics must not change bridge results or leak exceptions.
        return
