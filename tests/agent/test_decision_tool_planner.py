"""DP16 synthetic protocol/cache/recovery fixtures never qualify a release."""
from dataclasses import asdict, replace
import json
import threading
import time

import pytest

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError, ModelBundle, digest
from agent.decisions.point_adapters import Effect
from agent.decisions.point_policies import PointResolution
from agent.decisions.policy import PointPolicy
from agent.decisions.tool_planner import BRIDGES, BundleSession, LiveCatalog, ToolPlanner

SCOPE = "a" * 64
BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)


def schema(name):
    return {"type": "function", "function": {"name": name, "description": name,
            "parameters": {"type": "object", "properties": {}}}}


def catalog(*names, scope=SCOPE, bridges=BRIDGES):
    return LiveCatalog.from_authorized([schema(name) for name in names], scope_digest=scope,
        families={"files": list(names)}, bridge_names=bridges)


def response(req, selected):
    record = req.to_record()
    keys = ("request_id", "point_id", "contract_version", "contract_digest", "question_id", "input_digest", "scope_digest")
    return {**{key: record[key] for key in keys}, **asdict(BUNDLE),
            "distribution": {key: float(key == selected) for key in req.live_options},
            "selected": selected, "unclear": selected == "unclear", "latency_ms": .1}


def run_plan(choices=None, *, mode="shadow", lookup=None, classification="synthetic", transport=None):
    choices = choices or {"need": "needs_tools", "effort": "four_plus", "family": "yes", "tool": "read", "verify": "yes"}
    seen, lock = [], threading.Lock()
    receipts = []
    def infer(req, timeout):
        with lock:
            seen.append(req)
        return response(req, choices[req.question_id])
    client = DecisionClient(bundle=BUNDLE, transport=transport or infer, sink=receipts.append,
                            policies={"DP16": PointPolicy(mode, timeout_seconds=.5)})
    planner = ToolPlanner(client, catalog_lookup=lookup or (lambda: catalog("read", "write")))
    return planner.plan(request="synthetic request", scope_digest=SCOPE, deadline=time.time() + 3,
                        classification=classification), seen, receipts


def test_causal_protocol_batches_independent_questions_before_live_choice_and_verify():
    plan, seen, receipts = run_plan()
    questions = [req.question_id for req in seen]
    assert set(questions[:3]) == {"need", "effort", "family"}
    assert questions[3:] == ["tool", "verify"]
    tool = next(req for req in seen if req.question_id == "tool")
    verify = next(req for req in seen if req.question_id == "verify")
    assert tool.live_options == ("read", "write", "none", "unclear")
    assert json.loads(verify.state_packet.state_json)["selected_tool"] == "read"
    assert plan.verified_tool_ids == ("read",) and plan.families == ("files",)
    assert plan.effort_bucket == "four_plus"  # A hint; no budget or tool count cap exists.
    assert plan.mode == "shadow" and all(item["actual_route"] == "incumbent" for item in receipts)
    assert len(plan.decision_receipt_ids) == len(receipts)


def test_no_tools_keeps_bridge_and_skips_conditional_tool_stages():
    plan, seen, _ = run_plan({"need": "no_tools", "effort": "one", "family": "no"})
    assert plan.need == "no_tools" and plan.verified_tool_ids == ()
    assert plan.reopen_policy == "authorized_search_describe_call"
    assert {req.question_id for req in seen} == {"need", "effort", "family"}
    denied, seen, _ = run_plan(lookup=lambda: catalog("read", bridges=("tool_search",)))
    assert denied.fallback == "authorized_bridge_required" and not seen


def test_off_private_outage_unclear_and_stale_catalog_use_incumbent():
    off, seen, _ = run_plan(mode="off")
    assert off.fallback == "off" and not seen
    private, seen, receipts = run_plan(classification="private")
    assert private.fallback == "privacy_not_qualified" and not seen
    assert receipts and all(item["distribution"] is None for item in receipts)
    unclear, _, _ = run_plan({"need": "unclear", "effort": "defer", "family": "no"})
    assert unclear.fallback == "unclear" and unclear.verified_tool_ids == ()
    calls = []
    def changing():
        calls.append(1)
        return catalog("read", "write") if len(calls) == 1 else catalog("read")
    stale, seen, _ = run_plan(lookup=changing)
    assert stale.fallback == "stale_catalog" and {req.question_id for req in seen} == {"need", "effort", "family"}
    def unavailable(req, timeout):
        raise OSError("private exception text")
    failed, _, receipts = run_plan(transport=unavailable)
    assert failed.fallback in {"node_unavailable", "circuit_open"}
    assert "private exception" not in json.dumps(receipts)


def resolution(plan):
    return PointResolution("DP16", "enforce", Effect("bundle", plan.verified_tool_ids), None,
                           "accepted_within_floor", "5" * 64, "6" * 64, scope_digest=SCOPE)


def test_no_tools_false_negative_recovers_without_prefix_change_and_revocation_denies():
    plan, _, _ = run_plan({"need": "no_tools", "effort": "one", "family": "no"})
    plan = replace(plan, mode="enforce")  # Cache component test, no runtime release.
    session = BundleSession()
    full = catalog("read", "write")
    frozen = session.install(catalog=full, incumbent_schemas=full.definitions, context_id="context-1",
        boundary="new_context", plan=plan, resolution=resolution(plan))
    assert set(frozen.selected_tool_ids) == set(BRIDGES)
    before = frozen.schemas_json
    receipts = []
    recovered = session.reopen("read", catalog_lookup=lambda: full, receipt_sink=receipts.append)
    assert recovered["function"]["name"] == "read" and receipts[-1]["recovered"]
    assert session.bundle.schemas_json == before and session.bundle.prefix_digest == receipts[-1]["prefix_digest"]
    # Same-context planning, even after another user turn, never swaps schemas.
    candidate, _, _ = run_plan()
    assert session.install(catalog=full, incumbent_schemas=full.definitions, context_id="context-1",
        boundary="same_context", plan=replace(candidate, mode="enforce"), resolution=resolution(candidate)) is frozen
    with pytest.raises(DecisionError, match="not_authorized_or_unavailable"):
        session.reopen("read", catalog_lookup=lambda: catalog("write"), receipt_sink=receipts.append)
    assert not receipts[-1]["recovered"] and session.bundle.schemas_json == before
    assert "read" not in json.dumps(receipts[-1])
    with pytest.raises(DecisionError, match="planner_owner_mismatch"):
        session.reopen("read", catalog_lookup=lambda: catalog("read", scope="b" * 64), receipt_sink=receipts.append)


def test_only_permitted_context_boundary_can_install_qualified_bundle():
    plan, _, _ = run_plan()
    full = catalog("read", "write")
    session = BundleSession()
    shadow = session.install(catalog=full, incumbent_schemas=full.definitions, context_id="one",
        boundary="new_context", plan=plan, resolution=resolution(plan))
    assert shadow.schemas == full.definitions and shadow.plan_bundle_id is None
    with pytest.raises(DecisionError, match="new_context_boundary_required"):
        session.install(catalog=full, incumbent_schemas=full.definitions, context_id="one", boundary="new_context")
    candidate = replace(plan, mode="enforce")
    changed = session.install(catalog=full, incumbent_schemas=full.definitions, context_id="one",
        boundary="compression", plan=candidate, resolution=resolution(candidate))
    assert set(changed.selected_tool_ids) == set(BRIDGES) | {"read"}
    assert changed.schemas_json != shadow.schemas_json
    # A forged selection cannot expose a tool absent from verification/catalog.
    bad = replace(resolution(candidate), effective=Effect("bundle", ("secret_personal_mcp",)))
    with pytest.raises(DecisionError, match="unverified_bundle_tool"):
        session.install(catalog=full, incumbent_schemas=full.definitions, context_id="two",
            boundary="new_context", plan=candidate, resolution=bad)


@pytest.mark.parametrize("choices,fallback", [
    ({"need": "no_tools", "effort": "one", "family": "yes"}, "contradictory_tool_need"),
    ({"need": "needs_tools", "effort": "one", "family": "unclear"}, "unclear_family"),
    ({"need": "needs_tools", "effort": "one", "family": "yes", "tool": "none"}, "no_verified_tool"),
    ({"need": "needs_tools", "effort": "one", "family": "yes", "tool": "read", "verify": "no"}, "tool_verification_failed"),
])
def test_unclear_or_contradictory_stages_do_not_filter_tools(choices, fallback):
    plan, _, _ = run_plan(choices)
    assert plan.fallback == fallback and plan.need == "defer" and plan.verified_tool_ids == ()
