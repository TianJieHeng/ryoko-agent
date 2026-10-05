"""Family-summary projection stays closed and complete, bound to the snapshot."""
from dataclasses import replace
import json
import time

import pytest

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError, DecisionRequest, ModelBundle, StatePacket, canonical, digest
from agent.decisions.laya_codec import encode_laya_batch
from agent.decisions.laya_prompts import render_dp16_state
from agent.decisions.planner_context import build_planner_context
from agent.decisions.planner_v2 import family_summary_catalog
from agent.decisions.policy import PointPolicy
from agent.decisions.registry import contract_for
from agent.decisions.state import build_state
from agent.decisions.tool_planner import BRIDGES, LiveCatalog

SCOPE = "a" * 64


def view(names=("read", "write")):
    return LiveCatalog.from_authorized([{"type": "function", "function": {"name": name,
        "description": "A tool-specific purpose never copied to stage one", "parameters": {"type": "object"}}}
        for name in names], scope_digest=SCOPE, families={"files": names}, bridge_names=BRIDGES,
        family_descriptions={"files": "Authorized file operations"})


def states(catalog=None):
    catalog = view() if catalog is None else catalog
    context = build_planner_context("Inspect project files", scope_digest=SCOPE, classification="synthetic")
    shared = json.loads(render_dp16_state(context, catalog=family_summary_catalog(catalog), stage=1))
    return tuple(build_state("DP16", {**shared, **target}, scope_digest=SCOPE,
                 classification="synthetic", contract_version=2)
                 for target in ({}, {}, {"selected_family": catalog.family_alias("files")}))


def requests():
    contract = contract_for("DP16", 2)
    return tuple(DecisionRequest("DP16", 2, contract.contract_digest, question, packet,
        contract.question(question).options, 2_000_000_000.0, digest(index))
        for index, (question, packet) in enumerate(zip(("need", "effort", "family"), states())))


def test_summary_projection_binds_all_members_without_copying_tool_descriptors():
    live = view()
    projected = family_summary_catalog(live)
    assert projected["version"] == live.version and projected["scope_digest"] == live.scope_digest
    assert projected["tools"] == [] and projected["families"][0]["member_count"] == 2
    assert "tool-specific" not in canonical(projected)
    assert projected["families"][0]["membership_digest"] == digest({"family": live.family_alias("files"),
        "members": sorted(live.tool_alias(name) for name in live.tool_ids)})
    changed = family_summary_catalog(view(("read",)))
    assert changed["version"] != projected["version"]
    assert changed["families"][0]["membership_digest"] != projected["families"][0]["membership_digest"]
    assert len(encode_laya_batch(requests()).requests) == 3


@pytest.mark.parametrize("indices", [(0,), (0, 1), (0, 2), (1, 2)])
def test_initial_summary_batch_requires_need_effort_and_every_family(indices):
    rows = requests()
    with pytest.raises(DecisionError, match="incomplete_family_stage"):
        encode_laya_batch(tuple(rows[index] for index in indices))


@pytest.mark.parametrize("change", [
    lambda catalog: catalog.update(projection="unversioned"),
    lambda catalog: catalog.update(bridges=["unauthorized_bridge"]),
    lambda catalog: catalog.update(tools=[{"description": "unnecessary tool data"}]),
    lambda catalog: catalog["families"][0].update(member_count=True),
    lambda catalog: catalog["families"][0].update(member_count=1025),
    lambda catalog: catalog["families"][0].update(membership_digest="not-a-digest"),
    lambda catalog: catalog["families"][0].update(tools=[]),
    lambda catalog: catalog["families"].append(dict(catalog["families"][0])),
])
def test_malformed_summary_shape_is_not_an_escape_from_catalog_validation(change):
    catalog = family_summary_catalog(view())
    change(catalog)
    context = build_planner_context("Inspect files", scope_digest=SCOPE, classification="synthetic")
    with pytest.raises(DecisionError):
        render_dp16_state(context, catalog=catalog, stage=1)


def test_summary_shape_is_stage_one_only_and_cannot_bind_tools_or_prior_choices():
    context = build_planner_context("Inspect files", scope_digest=SCOPE, classification="synthetic")
    catalog = family_summary_catalog(view())
    for stage in (2, 3):
        with pytest.raises(DecisionError, match="invalid_laya_catalog"):
            render_dp16_state(context, catalog=catalog, stage=stage)
    with pytest.raises(DecisionError, match="invalid_question_binding"):
        render_dp16_state(context, catalog=catalog, stage=1,
                          bindings={"q": {"selected_family": view().family_alias("files"), "menu": []}})
    with pytest.raises(DecisionError, match="invalid_laya_prior"):
        render_dp16_state(context, catalog=catalog, stage=1, prior={"need": "needs_tools"})


def test_native_client_refuses_full_descriptor_stage_one_before_transport():
    live = view()
    context = build_planner_context("Inspect project files", scope_digest=SCOPE, classification="synthetic")
    packet = StatePacket(render_dp16_state(context, catalog=live.descriptor_values, stage=1), SCOPE, "synthetic")
    class Transport:
        protocol = "laya_systemone"
        admission_key = "must-not-dispatch-full-stage-one"
        def decide_many(self, *args):
            pytest.fail("full stage-one catalog leaked")
    client = DecisionClient(bundle=ModelBundle("1"*64, "2"*64, "3"*64), transport=Transport(),
        policies={"DP16": PointPolicy("shadow")}, sink=lambda row: None)
    out = client.decide_many("DP16", (packet,), 2, time.time()+1, question_ids=("need",))
    assert out.fallback == "invalid_response_schema" and out.metrics.batch_count == 0
