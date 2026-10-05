"""Real owned DP16 observer/recovery seams; shipped default-off and non-mutating."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time

from agent.decisions.contracts import digest, require
from agent.decisions.planner_runtime_context import owner_planner_context
from agent.decisions.receipts import scope_digest
from agent.decisions.tool_planner import ToolPlanner, live_catalog


class PolicyJournal:
    """The owner/run fence and closed wire schema are rechecked for every write."""
    durable = True

    def __init__(self, run):
        self.run = run

    def __call__(self, record):
        with self.run.control_lock:
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
    from tools.agent_policy_gate import authorize_tool
    require(type(request_definitions) in (list, tuple), "invalid_catalog_schema")
    definitions = {}
    for item in request_definitions:
        require(isinstance(item, dict) and isinstance(item.get("function"), dict)
                and isinstance(item["function"].get("name"), str), "invalid_catalog_schema")
        name = item["function"]["name"]
        if authorize_tool(name, context=run.context) is not None:
            continue
        require(name not in definitions, "duplicate_catalog_tool")
        definitions[name] = item
    if view is not None:
        require(view.session_policy_version == run.context.policy.digest, "planner_owner_mismatch")
        discoverable = set(view.discoverable_tool_ids)
        for metadata in registry.catalog_metadata():
            if (metadata.name in discoverable and metadata.name not in definitions
                    and authorize_tool(metadata.name, context=run.context) is None):
                definitions[metadata.name] = {"type": "function", "function": json.loads(metadata.schema_json)}
    return list(definitions.values())


@dataclass(frozen=True)
class PreparedFrontDoor:
    run_id: str
    turn_id: str
    generation: int
    session_id: str
    scope_digest: str
    request_digest: str
    catalog: object
    plan: object
    client: object
    context: object
    need_outcome: object = None

    @property
    def planner_context(self):
        return self.context


def _current_fence(run):
    from agent.runtime_commands import assert_runtime_dispatch
    from tools.capability_broker import require_live_policy
    require(assert_runtime_dispatch(run.agent) is run and require_live_policy() == run.context,
            "planner_owner_mismatch")


def _request_digest(request, turn_id):
    from agent.decisions.planner_runtime_context import _visible_request
    text, attachments = _visible_request(request, turn_id)
    return digest({"request": text, "attachments": attachments})


def prepared_for_turn(run, *, request, turn_id=""):
    turn_id = turn_id or getattr(run.agent, "_current_turn_id", "") or run.run_id
    saved = getattr(run.agent, "_decision_prepared_frontdoor", None)
    if (isinstance(saved, PreparedFrontDoor) and saved.run_id == run.run_id
            and saved.generation == run.generation and saved.session_id == run.agent.session_id
            and saved.turn_id == turn_id and saved.scope_digest == scope_digest(run.context)
            and saved.request_digest == _request_digest(request, turn_id)):
        _current_fence(run)
        return saved
    return None


def prepare_front_door(run, client, *, request_definitions, request, history=(), turn_id="", goal=""):
    """One owned planner operation, including context/catalog and durable receipts.

    The same bound result may be consumed by an earlier cache owner and the later
    observer, but never by another run, turn, generation or current user request.
    """
    from agent.decisions.integration import _observe_with_budget
    from tui_gateway.contracts.decision_plans import DecisionToolPlan
    policy = client.policy("DP16")
    if policy.mode == "off":
        return None
    _current_fence(run)
    saved = prepared_for_turn(run, request=request, turn_id=turn_id)
    if saved is not None:
        return saved
    turn_id = turn_id or getattr(run.agent, "_current_turn_id", "") or run.run_id
    attempt = (run.run_id, run.generation, turn_id)
    if getattr(run.agent, "_decision_frontdoor_attempted", None) == attempt:
        return None
    run.agent._decision_frontdoor_attempted = attempt
    deadline = time.time() + policy.timeout_seconds
    if run.budget is not None:
        deadline = min(deadline, run.budget.deadline)
    scope = scope_digest(run.context)

    def operation():
        _current_fence(run)
        if policy.mode != "enforce":
            record_observer_policy(run, "DP16", policy)
        context = owner_planner_context(run, request=request, history=history, turn_id=turn_id, goal=goal)
        lookup = lambda: live_catalog(_authorized_definitions(run, request_definitions), scope_digest=scope)
        catalog = lookup()
        planner = ToolPlanner(client, catalog_lookup=lookup, fence=lambda: _current_fence(run))
        values = context.values
        plan = planner.plan(request=values.get("request", ""), goal=goal if isinstance(goal, str) else "",
            scope_digest=scope, deadline=deadline, classification=context.classification,
            planner_context=context if client.native_batches else None)
        flags = values.get("context_flags", {})
        status = ("privacy_blocked" if context.classification == "private" else
                  "transport_attempted" if plan.metrics.batch_count else "not_attempted")
        plan = replace(plan, context_digest=digest(values),
            renderer_version="dp16-systemone-v1" if plan.protocol_version == 2 else None,
            context_redactions=flags.get("redactions", 0),
            context_omissions=flags.get("omitted_messages", 0) + flags.get("omitted_outcomes", 0)
                             + len(flags.get("truncated_fields", ())), authorization_status=status)
        payload = DecisionToolPlan.model_validate(plan.to_record()).model_dump(mode="json")
        prepared = PreparedFrontDoor(run.run_id, turn_id, run.generation, run.agent.session_id,
            scope, _request_digest(request, turn_id), catalog, plan, client, context)
        with run.control_lock:
            _current_fence(run)
            require(client.policy("DP16") == policy, "planner_policy_changed")
            # The runtime sink checks run cancellation/command ownership before
            # this append, not only the lease generation checked by SessionDB.
            run.db.append_runtime_event(run.session_id, "decision.tool_plan", payload,
                holder=run.holder, generation=run.generation, run_id=run.run_id)
            run.agent._decision_tool_plan = (run.run_id, plan, digest(request_definitions))
            run.agent._decision_prepared_frontdoor = prepared
        return prepared

    def remote_unknown():
        key = getattr(client.transport, "admission_key", None)
        return bool(client.native_batches and isinstance(key, str)
                    and client._batch_admission.remote_unknown(key))

    return _observe_with_budget(run, deadline, operation, completion_unknown=remote_unknown)


def observe_front_door(run, client, *, request_definitions, request, goal="", history=(), turn_id=""):
    """Late observer consumes an early owner result or plans once in shadow."""
    saved = prepared_for_turn(run, request=request, turn_id=turn_id)
    if saved is not None:
        return saved.plan
    require(client.policy("DP16").mode != "enforce", "owner_consumer_not_qualified")
    prepared = prepare_front_door(run, client, request_definitions=request_definitions,
        request=request, goal=goal, history=history, turn_id=turn_id)
    return prepared.plan if prepared is not None else None


def observe_bridge_recovery(requested_names, described_names, *, current_tool_defs):
    """Record real frozen-bundle misses, or separately labeled shadow omissions.

    A restored/rolled-back context can retain its reduced request prefix without
    a current-turn plan. Its persisted installation is the source of that prefix;
    a newly proposed shortlist cannot replace those bytes or certify a real miss.
    The existing bridge still owns recovery and independently authorizes dispatch.
    """
    try:
        from agent.runtime_commands import assert_runtime_dispatch
        run = assert_runtime_dispatch()
        scope = scope_digest(run.context)
        owner = getattr(run.agent, "_decision_bundle_owner", None)
        record = getattr(owner, "record", None)
        installed = record is not None and record.get("plan_bundle_id") is not None
        if installed:
            from hermes_state_decision_bundles import owner_digest, read_bundle_state, validate_record
            require(owner.session_id == run.agent.session_id and owner.owner_digest == owner_digest(run),
                    "planner_owner_mismatch")
            record = validate_record(record, run)
            durable, _ = read_bundle_state(run)
            require(durable is not None and durable["record_digest"] == record["record_digest"],
                    "bundle_pin_changed")
            selected = {item["function"]["name"] for item in json.loads(record["schemas_json"])}
            bundle_id, previous_catalog = record["plan_bundle_id"], record["catalog_version"]
            prefix_digest, observation_only = record["prefix_digest"], False
        else:
            saved = getattr(run.agent, "_decision_tool_plan", None)
            if saved is None or saved[0] != run.run_id:
                return
            _, plan, prefix_digest = saved
            # An enforcing proposal that failed installation is not an actual
            # reduced prefix. Keep the legacy shadow/advisory diagnostic only.
            if (plan.mode == "enforce" or plan.fallback is not None
                    or plan.need not in {"no_tools", "needs_tools"}):
                return
            require(plan.scope_digest == scope, "planner_owner_mismatch")
            selected = set(plan.verified_tool_ids)
            bundle_id, previous_catalog, observation_only = plan.bundle_id, plan.live_catalog_version, True
        current = live_catalog(current_tool_defs, scope_digest=scope)
        live, described = set(current.tool_ids), set(described_names)
        from agent.decisions.tool_planner import BRIDGES
        bridge_ready = not installed or set(current.bridge_names) == set(BRIDGES)
        for name in set(requested_names) - selected:
            recovered = bridge_ready and name in described and name in live
            # Rejected names remain digests, never another owner's catalog oracle.
            PolicyJournal(run)({"kind": "planner_miss", "scope_digest": scope,
                "bundle_id": bundle_id, "catalog_version": current.version,
                "previous_catalog_version": previous_catalog, "tool_digest": digest(name),
                "recovered": recovered, "reason": "authorized_reopen" if recovered else "not_authorized_or_unavailable",
                "prefix_digest": prefix_digest, "observation_only": observation_only})
    except Exception:
        # Optional diagnostics must not change bridge results or leak exceptions.
        return
