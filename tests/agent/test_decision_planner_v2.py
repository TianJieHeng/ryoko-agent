"""End-to-end synthetic DP16 v2 native codec, planner and receipt projection."""
from dataclasses import replace
import json
import time
import uuid

import pytest

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import ModelBundle, canonical
from agent.decisions.laya_codec import decode_laya_batch, encode_laya_batch
from agent.decisions.planner_context import build_planner_context
from agent.decisions.policy import PointPolicy
from agent.decisions.tool_planner import BRIDGES, LiveCatalog, ToolPlanner
from tui_gateway.contracts.decision_plans import DecisionToolPlan

SCOPE = "a" * 64
BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)


def catalog(families=None, *, bridges=BRIDGES, description="Read or modify project content"):
    families = families or {"files": ("read", "write")}
    definitions = [{"type": "function", "function": {"name": name, "description": description,
                    "parameters": {"type": "object", "properties": {}}}}
                   for names in families.values() for name in names]
    return LiveCatalog.from_authorized(definitions, scope_digest=SCOPE,
                                      families=families, bridge_names=bridges,
                                      family_descriptions={family: "Authorized operations" for family in families})


class NativeFixture:
    protocol = "laya_systemone"

    def __init__(self, choose=None, *, after=None):
        self.admission_key = "fixture-planner:" + uuid.uuid4().hex
        self.calls, self.choose, self.after = [], choose, after

    def decide_many(self, requests, timeout):
        batch = encode_laya_batch(requests, model_alias="synthetic-fixture")
        self.calls.append((requests, batch))
        answers = {}
        for binding in batch.bindings:
            request = binding.request
            choice = ({"need": "needs_tools", "effort": "two_three"}.get(request.question_id, "yes")
                      if self.choose is None else self.choose(request))
            answers[binding.wire_qid] = {"type": "choice", "choice": choice, "confidence": 1.0,
                "probabilities": {name: float(choice == name) for name in request.live_options}}
        result = decode_laya_batch(canonical({"model": "synthetic-fixture", "answers": answers,
            "usage": {"input_tokens": 10, "output_tokens": len(answers)}}).encode(), batch, BUNDLE, latency_ms=.1)
        if self.after is not None:
            self.after(len(self.calls))
        return result


def run(*, view=None, choose=None, lookup=None, after=None, sink=None, fence=None, context=None, deadline=None):
    view = view or catalog()
    transport, receipts = NativeFixture(choose, after=after), []
    client = DecisionClient(bundle=BUNDLE, transport=transport, sink=receipts.append if sink is None else sink,
                            policies={"DP16": PointPolicy("shadow", timeout_seconds=.5)})
    planner = ToolPlanner(client, catalog_lookup=lookup or (lambda: view), fence=fence)
    plan = planner.plan(request="Read project content then write a summary", scope_digest=SCOPE,
        deadline=time.time()+3 if deadline is None else deadline, classification="synthetic", planner_context=context)
    return plan, transport, receipts


def test_two_tools_in_same_family_are_independently_included_then_jointly_verified():
    plan, transport, receipts = run()
    assert plan.fallback is None and plan.protocol_version == 2
    assert plan.verified_tool_ids == ("read", "write") and plan.families == ("files",)
    assert [len(requests) for requests, _ in transport.calls] == [3, 2, 2]
    assert [{request.question_id for request in requests} for requests, _ in transport.calls] == [
        {"need", "effort", "family"}, {"include"}, {"verify"}]
    assert len(plan.decision_receipt_ids) == len(receipts) == 7
    assert len({row["receipt_id"] for row in receipts}) == 7
    assert plan.metrics.batch_count == 3 and plan.metrics.question_count == 7
    assert plan.metrics.input_tokens == 30 and plan.metrics.output_tokens == 7
    assert all(row["actual_route"] == "incumbent" for row in receipts)
    verified = [json.loads(request.state_packet.state_json) for request in transport.calls[2][0]]
    shortlist = verified[0]["shortlist"]
    assert len(shortlist) == 2 and all(state["shortlist"] == shortlist for state in verified)
    assert {state["selected_tool"] for state in verified} == set(shortlist)
    assert all(state["prior"]["selected_tools"] == shortlist for state in verified)
    assert len({request.state_packet.input_digest for request in transport.calls[2][0]}) == 2
    projected = DecisionToolPlan.model_validate(plan.to_record())
    assert projected.metrics.question_count == 7 and projected.protocol_version == 2


def test_real_unicode_tool_identity_is_resolved_only_through_immutable_alias_map():
    view = catalog({"file family / authorized": ("読み取り/🗃️", "write file with spaces")})
    plan, transport, _ = run(view=view)
    assert set(plan.verified_tool_ids) == set(view.tool_ids)
    assert plan.fallback is None and DecisionToolPlan.model_validate(plan.to_record())
    body = transport.calls[1][1].body.decode()
    assert "write file with spaces" not in body and all("t_" in request.state_packet.state_json
                                                       for request in transport.calls[1][0])


@pytest.mark.parametrize("changed,choice,fallback,stages", [
    ("need", "unclear", "unclear", 1),
    ("need", "defer", "unclear_need", 1),
    ("effort", "unclear", "unclear_effort", 1),
    ("effort", "defer", "unclear_effort", 1),
    ("family", "unclear", "unclear_family", 1),
    ("family", "no", "no_verified_family", 1),
    ("include", "unclear", "unclear_tool", 2),
    ("include", "no", "no_verified_tool", 2),
    ("verify", "no", "tool_verification_failed", 3),
    ("verify", "unclear", "tool_verification_failed", 3),
])
def test_any_uncertainty_or_negative_verification_preserves_whole_incumbent(changed, choice, fallback, stages):
    def choose(request):
        return choice if request.question_id == changed else {
            "need": "needs_tools", "effort": "two_three"}.get(request.question_id, "yes")
    plan, transport, _ = run(choose=choose)
    assert plan.fallback == fallback and len(transport.calls) == stages
    assert plan.need == "defer" and not plan.verified_tool_ids and not plan.families


def test_no_tools_requires_every_family_no_and_preserves_authorized_bridge():
    def choose(request):
        return {"need": "no_tools", "effort": "one", "family": "no"}[request.question_id]
    plan, transport, _ = run(choose=choose)
    assert plan.fallback is None and plan.need == "no_tools" and len(transport.calls) == 1
    assert not plan.verified_tool_ids and plan.reopen_policy == "authorized_search_describe_call"
    plan, _, _ = run(choose=lambda request: "yes" if request.question_id == "family" else choose(request))
    assert plan.fallback == "contradictory_tool_need"
    denied, transport, _ = run(view=catalog(bridges=("tool_search",)))
    assert denied.fallback == "authorized_bridge_required" and not transport.calls


def test_inclusion_selects_each_candidate_and_never_silently_takes_first_four():
    plan, transport, _ = run(view=catalog({"files": tuple(f"tool{index}" for index in range(5))}))
    assert plan.fallback == "bounded_shortlist_exceeded" and not plan.verified_tool_ids
    assert [len(requests) for requests, _ in transport.calls] == [3, 5]


def test_total_shortlist_overflow_is_all_or_nothing():
    families = {f"family{index}": tuple(f"tool{index}_{j}" for j in range(4 if index < 3 else 1))
                for index in range(4)}
    plan, transport, _ = run(view=catalog(families, description="Capability"))
    assert plan.fallback == "bounded_shortlist_exceeded" and not plan.verified_tool_ids
    assert len(transport.calls) == 2


def test_family_and_byte_overflow_do_not_truncate_or_transmit():
    view = catalog({f"family{index}": (f"tool{index}",) for index in range(17)})
    plan, transport, _ = run(view=view)
    assert plan.fallback == "bounded_menu_exceeded" and not transport.calls
    huge = catalog({"files": tuple(f"tool{index}" for index in range(32))}, description="x"*768)
    plan, transport, _ = run(view=huge)
    assert plan.fallback == "laya_state_bytes_exceeded" and len(transport.calls) == 1


def test_catalog_revocation_after_inference_prevents_dependent_stage():
    before, revoked, state = catalog(), catalog({"files": ("read",)}), {"revoked": False}
    plan, transport, _ = run(lookup=lambda: revoked if state["revoked"] else before,
                            after=lambda _: state.update(revoked=True))
    assert plan.fallback == "stale_catalog" and len(transport.calls) == 1
    assert plan.live_catalog_version == revoked.version and not plan.verified_tool_ids


def test_run_fence_checked_before_and_after_each_inference():
    state = {"superseded": False}
    def fence():
        if state["superseded"]:
            raise RuntimeError("private generation details")
    plan, transport, receipts = run(fence=fence, after=lambda _: state.update(superseded=True))
    assert plan.fallback == "planner_fence_failed" and len(transport.calls) == 1
    assert not plan.verified_tool_ids and not receipts


def test_receipt_failure_rejects_shadow_plan_and_stops_dependent_stage():
    def fail(row):
        raise RuntimeError("private write error")
    plan, transport, receipts = run(sink=fail)
    assert plan.fallback == "receipt_unavailable" and len(transport.calls) == 1
    assert not plan.decision_receipt_ids and not plan.verified_tool_ids


def test_prebuilt_private_context_is_never_downgraded_by_call_argument():
    context = build_planner_context("Inspect project files", scope_digest=SCOPE, classification="private")
    plan, transport, _ = run(context=context)
    assert plan.fallback == "privacy_not_qualified" and not transport.calls


def test_context_ambiguity_and_expired_deadline_prevent_transmission():
    context = build_planner_context("Do that again", scope_digest=SCOPE, classification="synthetic")
    plan, transport, _ = run(context=context)
    assert plan.fallback == "planner_reference_unresolved" and not transport.calls
    plan, transport, _ = run(deadline=time.time()-1)
    assert plan.fallback == "deadline_exceeded" and not transport.calls


def test_expired_original_deadline_after_receipts_cannot_be_reset_for_stage_two():
    def slow_receipt(row):
        time.sleep(.012)
    plan, transport, _ = run(sink=slow_receipt, deadline=time.time()+.025)
    assert plan.fallback in {"receipt_unavailable", "deadline_exceeded"}
    assert len(transport.calls) == 1 and not plan.verified_tool_ids


def test_legacy_receipt_projection_remains_versioned_and_v2_allows_full_62_trace():
    plan, _, _ = run()
    payload = plan.to_record()
    payload["decision_receipt_ids"] = [f"{index:064x}" for index in range(62)]
    assert len(DecisionToolPlan.model_validate(payload).decision_receipt_ids) == 62
    payload["protocol_version"] = 1
    with pytest.raises(ValueError, match="invalid_v1_plan_bounds"):
        DecisionToolPlan.model_validate(payload)


def test_candidate_overflow_stops_before_any_inclusion_and_never_takes_first_32():
    view = catalog({"files": tuple(f"tool{index}" for index in range(33))}, description="x")
    plan, transport, _ = run(view=view)
    assert plan.fallback == "bounded_candidate_exceeded"
    assert len(transport.calls) == 1 and not plan.verified_tool_ids


def test_twelve_selected_tools_across_three_families_keep_all_receipts():
    view = catalog({f"family{index}": tuple(f"tool{index}_{j}" for j in range(4))
                    for index in range(3)}, description="Capability")
    plan, transport, receipts = run(view=view)
    assert plan.fallback is None and len(plan.verified_tool_ids) == 12
    assert [len(requests) for requests, _ in transport.calls] == [5, 12, 12]
    assert len(plan.decision_receipt_ids) == len(receipts) == 29
    assert plan.metrics.question_count == 29


def test_policy_drift_after_inference_stops_the_complete_plan():
    view, receipts = catalog(), []
    client = None
    def change_policy(_):
        client.policies["DP16"] = PointPolicy("off")
    transport = NativeFixture(after=change_policy)
    client = DecisionClient(bundle=BUNDLE, transport=transport, sink=receipts.append,
                            policies={"DP16": PointPolicy("shadow", timeout_seconds=.5)})
    plan = ToolPlanner(client, catalog_lookup=lambda: view).plan(request="Inspect project files",
        scope_digest=SCOPE, deadline=time.time()+2, classification="synthetic")
    assert plan.fallback == "planner_policy_changed" and len(transport.calls) == 1
    assert not plan.verified_tool_ids


def test_family_summary_stage_handles_large_catalog_without_exporting_tool_descriptions():
    families = {"small": ("read", "write"), "large": tuple(f"bulk_{index}" for index in range(100))}
    view = catalog(families, description="PRIVATE-TOOL-DESCRIPTION-NOT-NEEDED-FOR-STAGE-ONE")
    small = view.family_alias("small")
    def choose(request):
        if request.question_id == "family":
            return "yes" if json.loads(request.state_packet.state_json)["selected_family"] == small else "no"
        return {"need": "needs_tools", "effort": "two_three"}.get(request.question_id, "yes")
    plan, transport, _ = run(view=view, choose=choose)
    assert plan.fallback is None and plan.verified_tool_ids == ("read", "write")
    first = json.loads(transport.calls[0][1].state_json)["catalog"]
    assert first["projection"] == "family_summaries_v1" and first["tools"] == []
    assert sorted(family["member_count"] for family in first["families"]) == [2, 100]
    assert all(set(family) == {"id", "description", "member_count", "membership_digest"}
               for family in first["families"])
    assert b"PRIVATE-TOOL-DESCRIPTION" not in transport.calls[0][1].body
    assert b"bulk_" not in transport.calls[0][1].body
    second = json.loads(transport.calls[1][1].state_json)["catalog"]
    assert len(second["tools"]) == 2 and {tool["family"] for tool in second["tools"]} == {small}
