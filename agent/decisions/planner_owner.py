"""The request-schema owner: first freeze and committed compression only.

Production configuration still admits no enforcement. A trusted host can supply
an evidence-qualified policy book and its exact release/client binding; the
synthetic integration tests exercise that path without any remote service. The
full canonical discovery view is never replaced by the request's frozen subset.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging

from agent.decisions.contracts import ModelBundle, canonical, digest, require
from agent.decisions.point_policies import PointPolicyBook
from agent.decisions.receipts import scope_digest
from agent.decisions.tool_planner import BRIDGES, BundleSession, FrozenBundle, live_catalog
from hermes_state_decision_bundles import (
    actor_for, owner_digest, persist_bundle, read_bundle_state, read_plan_receipts, validate_record,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerQualification:
    """Host-only inputs. Neither config, messages nor classifier output builds this."""
    policy_book: PointPolicyBook
    expected_release: ModelBundle
    client_factory: object

    def __post_init__(self):
        require(isinstance(self.policy_book, PointPolicyBook)
                and isinstance(self.expected_release, ModelBundle)
                and callable(self.client_factory), "qualified_owner_required")


@dataclass
class _OwnerState:
    session_id: str
    owner_digest: str
    record: dict | None = None
    boundary: str | None = None
    witness: dict | None = None
    fence: tuple | None = None
    prepared: object = None
    request_digest: str | None = None
    restore_failed: bool = False


def _run(agent):
    from agent.runtime_commands import assert_runtime_dispatch
    from tools.capability_broker import require_live_policy
    run = assert_runtime_dispatch(agent)
    require(run is not None and require_live_policy() == run.context, "decision_owner_required")
    return run


def _fence(run):
    return run.run_id, getattr(run.agent, "_current_turn_id", ""), run.generation


def _owner_key(agent):
    from agent.runtime_context import AgentContext
    context = getattr(agent, "runtime_context", None)
    if not isinstance(context, AgentContext):
        return None
    return digest({"actor": {key: getattr(context.identity, key)
                             for key in ("principal_id", "profile_id", "agent_id")},
                   "profile_home": digest(context.profile_home)})


def capture_turn_boundary(agent, *, conversation_history=()):
    """Capture first-freeze evidence, distinguishing absence from failed restore."""
    key = _owner_key(agent)
    if key is None:
        return
    previous = getattr(agent, "_decision_bundle_owner", None)
    retained = (previous.record if isinstance(previous, _OwnerState)
                and previous.session_id == agent.session_id and previous.owner_digest == key else None)
    # Publish the unknown state BEFORE attempting any authority/store read. A
    # transient error cannot turn a cold restart into an unpinned full request.
    state = _OwnerState(agent.session_id, key, retained, restore_failed=True)
    agent._decision_bundle_owner = state
    run = None
    try:
        run = _run(agent)
        record, pristine = read_bundle_state(run)
        state.record, state.restore_failed = record, False
        if pristine and not conversation_history and getattr(agent, "api_mode", None) == "chat_completions":
            state.boundary, state.witness, state.fence = "new_context", {"first_request": True}, _fence(run)
    except Exception:
        # Only exact, already-known bytes may survive a failed read. A cold or
        # malformed pin must be restored before request_schemas permits dispatch.
        if retained is not None and run is not None:
            try:
                validate_record(retained, run)
            except Exception:
                state.record = None


def committed_compaction(agent, *, session_commit_succeeded, old_session_id, compacted_in_place):
    """A rebuild attempt is not a boundary; only the durable commit can mint one."""
    if not (session_commit_succeeded and (old_session_id or compacted_in_place)):
        return
    state = getattr(agent, "_decision_bundle_owner", None)
    if not isinstance(state, _OwnerState):
        return
    prepared = state.prepared
    state.prepared, state.boundary, state.witness, state.fence = None, None, None, None
    try:
        run = _run(agent)
        require(getattr(agent, "api_mode", None) == "chat_completions", "owner_consumer_not_qualified")
        require(state.owner_digest == owner_digest(run), "planner_owner_mismatch")
        projection = run.db.read_context_projection(agent.session_id, actor_for(run))
        require(projection is not None and projection["generation"] == run.generation,
                "committed_boundary_required")
        witness = {key: projection[key] for key in ("projection_id", "checkpoint_id", "generation")}
        require(not state.record or state.record["boundary_witness"] != witness, "boundary_already_consumed")
        state.session_id, state.boundary, state.witness, state.fence = agent.session_id, "compression", witness, _fence(run)
        # A mid-turn compression can consume THIS turn's already prepared plan.
        # Manual/detached compaction has no admitted matching turn and cannot reuse it.
        if prepared is not None and _prepared_matches(prepared, run):
            state.prepared = prepared
    except Exception:
        logger.debug("DP16 compression boundary unavailable; retaining request prefix")


def _prepared_matches(prepared, run):
    return (prepared.run_id == run.run_id and prepared.generation == run.generation
            and prepared.session_id == run.agent.session_id
            and prepared.turn_id == getattr(run.agent, "_current_turn_id", "")
            and prepared.scope_digest == scope_digest(run.context))


def _qualified_client(run, state):
    qualification = getattr(run.agent, "_decision_bundle_qualification", None)
    if (state.restore_failed or not isinstance(qualification, OwnerQualification)
            or not state.boundary or state.fence != _fence(run)):
        return None
    book = qualification.policy_book
    policy, gate, bundle = book.client_settings("DP16", scope_digest=scope_digest(run.context))
    if (policy.mode != "enforce" or bundle != qualification.expected_release
            or "bundle" not in book.rule("DP16").allowed_effects):
        return None
    require(book.inspect("DP16", scope_digest=scope_digest(run.context))["contract_version"] == 2,
            "owner_contract_mismatch")
    client = qualification.client_factory(run, policy, gate, bundle)
    require(client.bundle == bundle and client.policy("DP16") == policy
            and client.gates.get("DP16") == gate, "decision_bundle_mismatch")
    return client


def prepare_turn_owner(agent, *, original_user_message, conversation_history, turn_id):
    """Plan the current user turn before any provider cache decoration."""
    from agent.decisions.integration import prepare_turn_planner
    state = getattr(agent, "_decision_bundle_owner", None)
    client = None
    try:
        if isinstance(state, _OwnerState):
            client = _qualified_client(_run(agent), state)
    except Exception:
        logger.debug("DP16 release unavailable; retaining incumbent request schemas")
    request_digest = None
    try:
        prepared = prepare_turn_planner(agent, original_user_message=original_user_message,
            conversation_history=conversation_history, turn_id=turn_id, client=client)
        if prepared is not None:
            from agent.decisions.planner_runtime import _request_digest
            request_digest = _request_digest(original_user_message, turn_id)
    except Exception:
        prepared = None
        logger.debug("DP16 planning unavailable; retaining incumbent request schemas")
    if isinstance(state, _OwnerState):
        state.request_digest, state.prepared = request_digest, prepared
        _apply_boundary(agent, state)
    return prepared


def _resolve(run, prepared, catalog, *, request_digest):
    from agent.decisions.client import DecisionOutcome
    from agent.decisions.point_adapters import Effect, PlannerFacts
    from agent.decisions.registry import contract_for
    qualification = getattr(run.agent, "_decision_bundle_qualification", None)
    require(isinstance(qualification, OwnerQualification), "qualified_owner_required")
    require(prepared is not None and _prepared_matches(prepared, run)
            and prepared.request_digest == request_digest, "stale_planner_turn")
    require(prepared.catalog.version == catalog.version
            and prepared.context.scope_digest == catalog.scope_digest
            and prepared.context.fallback is None
            and prepared.context.classification in {"synthetic", "public"}, "stale_planner_context")
    plan = prepared.plan
    require(plan.protocol_version == 2 and plan.mode == "enforce" and plan.fallback is None
            and plan.live_catalog_version == catalog.version and plan.scope_digest == catalog.scope_digest,
            "stale_catalog_or_plan")
    book = qualification.policy_book
    policy, gate, bundle = book.client_settings("DP16", scope_digest=catalog.scope_digest)
    require(policy.mode == "enforce" and gate is not None and bundle == qualification.expected_release
            and prepared.client.bundle == bundle and prepared.client.policy("DP16") == policy,
            "decision_bundle_mismatch")
    require(set(catalog.bridge_names) == set(BRIDGES), "authorized_bridge_required")
    receipts = read_plan_receipts(run, plan.decision_receipt_ids, plan_record=plan.to_record())
    expected = {**asdict(bundle), "point_id": "DP16", "scope_digest": catalog.scope_digest,
                "contract_version": 2, "contract_digest": contract_for("DP16", 2).contract_digest,
                "point_gate_digest": gate.gate_digest, "mode": "enforce", "actual_route": "qualified_recommendation"}
    for receipt in receipts:
        require(all(receipt.get(key) == value for key, value in expected.items()), "decision_bundle_mismatch")
        require(receipt.get("classification") in {"synthetic", "public"} and not receipt.get("unclear")
                and (receipt.get("distribution") or {}).get(receipt.get("selected"), 0)
                    >= policy.threshold_for(receipt.get("selected")), "qualified_durable_decision_required")
    need = [receipt for receipt in receipts if receipt.get("question_id") == "need"]
    require(len(need) == 1 and need[0]["selected"] == plan.need, "plan_need_receipt_mismatch")
    outcome = DecisionOutcome(plan.need, "qualified_recommendation", None, need[0], True)
    resolution = book.resolve("DP16", outcome=outcome,
        facts=PlannerFacts(catalog.tool_ids, plan.verified_tool_ids, True, True, True),
        incumbent=Effect("authorized_full", catalog.tool_ids), scope_digest=catalog.scope_digest)
    require(resolution.effective.action == "bundle", "qualified_resolution_required")
    return resolution, {**asdict(bundle), "gate_digest": gate.gate_digest,
                        "contract_version": 2, "policy_digest": resolution.policy_digest}


def _apply_boundary(agent, state):
    if not state.boundary:
        return
    try:
        if state.restore_failed:
            return
        if state.record is None and (state.prepared is None
                or state.prepared.plan.mode != "enforce" or state.prepared.plan.fallback is not None
                or not isinstance(getattr(agent, "_decision_bundle_qualification", None), OwnerQualification)):
            return  # No owner effect: avoid even rebuilding a catalog when off.
        run = _run(agent)
        require(state.owner_digest == owner_digest(run) and state.fence == _fence(run), "stale_owner_boundary")
        # Only the real full discoverable snapshot is authoritative, never the
        # previously reduced wire schemas or fresh omitted MCP metadata merged on resume.
        from agent.decisions.planner_runtime import _authorized_definitions
        catalog = live_catalog(_authorized_definitions(run, agent.tools), scope_digest=scope_digest(run.context))
        prepared = state.prepared
        resolution, release = None, None
        try:
            resolution, release = _resolve(run, prepared, catalog, request_digest=state.request_digest)
        except Exception:
            if state.boundary == "new_context" and state.record is None:
                return  # Off/shadow/unqualified preserves the exact incumbent.
        context_id = digest({"session": agent.session_id, "owner": state.owner_digest,
                             "boundary": state.boundary, "witness": state.witness})
        bundle_session = BundleSession()
        boundary = "new_context"
        if state.record is not None and state.record["scope_digest"] == catalog.scope_digest:
            previous = state.record
            bundle_session.bundle = FrozenBundle(previous["context_id"], previous["scope_digest"],
                previous["catalog_version"], previous["schemas_json"], previous["plan_bundle_id"],
                tuple(item["function"]["name"] for item in json.loads(previous["schemas_json"])))
            boundary = state.boundary
        # A committed projection can also begin a new policy-scoped BundleSession;
        # ordinary turns can never turn a policy revision into a cache boundary.
        bundle = bundle_session.install(catalog=catalog, incumbent_schemas=agent.tools,
            context_id=context_id, boundary=boundary, plan=prepared.plan if resolution else None,
            resolution=resolution)
        if resolution is None:
            # A failed selection must preserve the complete incumbent, including
            # schemas whose grants were revoked (dispatch still denies them).
            bundle = FrozenBundle(context_id, catalog.scope_digest, catalog.version, canonical(agent.tools), None,
                                  tuple(item["function"]["name"] for item in agent.tools))
        record = {"schema_version": 1, "actor": actor_for(run), "owner_digest": state.owner_digest,
            "session_id": agent.session_id, "run_id": run.run_id, "turn_id": getattr(agent, "_current_turn_id", ""),
            "generation": run.generation, "context_id": context_id, "boundary": state.boundary,
            "boundary_witness": state.witness, "scope_digest": catalog.scope_digest, "catalog_version": catalog.version,
            "schemas_json": bundle.schemas_json, "prefix_digest": bundle.prefix_digest,
            "plan_bundle_id": bundle.plan_bundle_id,
            "receipt_ids": list(prepared.plan.decision_receipt_ids) if resolution else [], "release": release}
        record["record_digest"] = digest(record)
        persist_bundle(run, record, expected_record_digest=(state.record or {}).get("record_digest"))
        state.record = record
    except Exception:
        logger.debug("DP16 bundle installation unavailable; retaining request prefix")
    finally:
        state.boundary, state.witness, state.fence = None, None, None


def request_schemas(agent):
    """Return undecorated request copies; retries and MCP refresh cannot expand them."""
    state = getattr(agent, "_decision_bundle_owner", None)
    if isinstance(state, _OwnerState) and state.session_id == getattr(agent, "session_id", None):
        if state.owner_digest != _owner_key(agent):
            return agent.tools
        if state.restore_failed and state.record is None:
            try:
                run = _run(agent)
                require(owner_digest(run) == state.owner_digest, "planner_owner_mismatch")
                record, _ = read_bundle_state(run)
                state.record, state.restore_failed = record, False
            except Exception:
                from agent.runtime_commands import RuntimeFenceError
                raise RuntimeFenceError("Request schema prefix could not be restored; retry this turn") from None
        if state.boundary == "compression":
            _apply_boundary(agent, state)
        if state.record is not None:
            return json.loads(state.record["schemas_json"])
    return agent.tools
