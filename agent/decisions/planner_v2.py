"""DP16 v2's three dependent native batches; no partial shortlist reductions."""
from __future__ import annotations

import json
import time

from agent.decisions.batching import PlannerMetrics
from agent.decisions.contracts import DecisionError, digest, require
from agent.decisions.laya_prompts import render_dp16_state
from agent.decisions.planner_context import PlannerContext, build_planner_context
from agent.decisions.state import build_state


def family_summary_catalog(catalog):
    """Stage one exposes purposes, with full membership bound but not exported.

    The immutable snapshot version still covers every descriptor and membership.
    No tool description, schema, tool alias list, or tool name is copied here.
    """
    aliases = {tool.tool_id: tool.alias for tool in catalog.tool_descriptors}
    return {"version": catalog.version, "scope_digest": catalog.scope_digest,
        "policy_digest": catalog.policy_digest, "tool_view_revision": catalog.tool_view_revision,
        "bridges": list(catalog.bridge_names), "projection": "family_summaries_v1", "tools": [],
        "families": [{"id": family.alias, "description": family.description,
            "member_count": len(family.tool_ids), "membership_digest": digest({"family": family.alias,
                "members": sorted(aliases[name] for name in family.tool_ids)})}
            for family in catalog.family_descriptors]}


class NativeToolPlanner:
    """A single bounded planning attempt, never an owner or a permission source."""
    def __init__(self, planner, *, request, scope_digest, deadline, goal, context,
                 classification, planner_context):
        self.client, self.lookup, self.fence = planner.client, planner.catalog_lookup, planner.fence
        self.scope, self.deadline = scope_digest, deadline
        self.started = time.monotonic()
        self.catalog = self.lookup()
        self.current = self.catalog
        self.policy, self.bundle = self.client.policy("DP16"), self.client.bundle
        self.outcomes, self.metrics = [], PlannerMetrics()
        self.effort = "defer"
        self.request, self.goal, self.context = request, goal, context
        self.classification, self.planner_context = classification, planner_context

    def finish(self, *, need="defer", families=(), tools=(), fallback=None):
        from agent.decisions.tool_planner import ToolPlan
        require(self.metrics.batch_count <= 3 and self.metrics.question_count <= 62, "planner_bounds_exceeded")
        receipt_ids = tuple(out.receipt["receipt_id"] for out in self.outcomes
                            if out.receipt is not None and out.receipt_persisted)
        require(len(receipt_ids) <= 62, "planner_bounds_exceeded")
        if fallback is None and len(receipt_ids) != len(self.outcomes):
            need, families, tools, fallback = "defer", (), (), "receipt_unavailable"
        material = {"need": need, "effort_bucket": self.effort, "families": families,
                    "verified_tool_ids": tools, "live_catalog_version": self.current.version,
                    "scope_digest": self.scope, "protocol_version": 2}
        return ToolPlan(need, self.effort, tuple(families), tuple(tools), self.current.version,
            digest(material), "authorized_search_describe_call", self.scope, self.policy.mode, fallback,
            receipt_ids, (time.monotonic() - self.started) * 1000, protocol_version=2, metrics=self.metrics)

    def _check_owner(self):
        from agent.decisions.tool_planner import BRIDGES
        self.client._fence(self.fence)
        require(time.time() < self.deadline, "deadline_exceeded")
        require(self.client.policy("DP16") == self.policy and self.client.bundle == self.bundle,
                "planner_policy_changed")
        self.current = self.lookup()
        require(self.current.scope_digest == self.scope, "planner_owner_mismatch")
        require(set(self.current.bridge_names) == set(BRIDGES), "authorized_bridge_required")
        require(self.current.version == self.catalog.version, "stale_catalog")

    def _stage_catalog(self, stage, families=None):
        if stage == 1:
            return family_summary_catalog(self.catalog)
        values = self.catalog.descriptor_values
        if families is not None:
            permitted = {self.catalog.family_alias(family) for family, _ in families}
            values["families"] = [family for family in values["families"] if family["id"] in permitted]
            values["tools"] = [tool for tool in values["tools"] if tool["family"] in permitted]
        return values

    def _stage(self, stage, jobs, *, families=None, prior=None):
        self._check_owner()
        require(self.metrics.batch_count < 3 and 1 <= len(jobs) <= 64, "planner_bounds_exceeded")
        shared = json.loads(render_dp16_state(self.planner_context,
            catalog=self._stage_catalog(stage, families), stage=stage, prior=prior))
        packets = tuple(build_state("DP16", {**shared, **target}, scope_digest=self.scope,
            classification=self.planner_context.classification, contract_version=2) for _, target in jobs)
        result = self.client.decide_many("DP16", packets, 2, self.deadline,
                                        question_ids=tuple(question for question, _ in jobs), fence=self.fence)
        self.outcomes.extend(result.outcomes)
        self.metrics = self.metrics.plus(result.metrics)
        require(result.fallback is None, result.fallback or "batch_failed")
        require(all(out.receipt_persisted for out in result.outcomes), "receipt_unavailable")
        self._check_owner()
        return result.outcomes

    def _selected(self, outcome):
        receipt = outcome.receipt or {}
        choice = receipt.get("selected")
        confidence = (receipt.get("distribution") or {}).get(choice, 0)
        if (not outcome.receipt_persisted or outcome.fallback not in {None, "shadow_observation"}
                or choice in {None, "unclear"} or confidence < self.policy.threshold_for(choice)):
            return None
        return choice

    def _target(self, family, names, tool=None, shortlist=None):
        target = {"selected_family": self.catalog.family_alias(family)}
        # Every candidate menu is already fully represented by its authoritative
        # family membership in the shared catalog. Bind its exact target without
        # multiplying that same menu by the number of questions on the wire.
        if tool is not None:
            target["selected_tool"] = self.catalog.tool_alias(tool)
        if shortlist is not None:
            target["shortlist"] = list(shortlist)
        return target

    def _run(self):
        from agent.decisions.tool_planner import BRIDGES
        require(self.catalog.scope_digest == self.scope, "planner_owner_mismatch")
        if self.policy.mode == "off":
            return self.finish(fallback="off")
        require(set(self.catalog.bridge_names) == set(BRIDGES), "authorized_bridge_required")
        require(len(self.catalog.families) <= 16, "bounded_menu_exceeded")
        if self.planner_context is None:
            require(type(self.context) is str, "invalid_planner_context")
            self.planner_context = build_planner_context(self.request, scope_digest=self.scope,
                goal=self.goal, constraints=(self.context,) if self.context else (), classification=self.classification)
        require(isinstance(self.planner_context, PlannerContext), "invalid_planner_context")
        require(self.planner_context.scope_digest == self.scope, "planner_owner_mismatch")
        require(self.planner_context.fallback is None, self.planner_context.fallback or "invalid_planner_context")
        first = self._stage(1, [("need", {}), ("effort", {})] + [
            ("family", self._target(family, names)) for family, names in self.catalog.families])
        need, effort = self._selected(first[0]), self._selected(first[1])
        require(need in {"no_tools", "needs_tools"},
                first[0].fallback if first[0].fallback not in {None, "shadow_observation"} else "unclear_need")
        require(effort in {"one", "two_three", "four_plus"}, "unclear_effort")
        self.effort = effort
        family_choices = tuple(self._selected(out) for out in first[2:])
        require(all(choice in {"yes", "no"} for choice in family_choices), "unclear_family")
        chosen = tuple(family for family, choice in zip(self.catalog.families, family_choices) if choice == "yes")
        if need == "no_tools":
            require(not chosen, "contradictory_tool_need")
            return self.finish(need=need)
        require(bool(chosen), "no_verified_family")
        candidates = tuple((family, names, tool) for family, names in chosen for tool in names)
        require(0 < len(candidates) <= 32, "bounded_candidate_exceeded")
        prior = {"need": need, "effort": effort,
                 "selected_families": [self.catalog.family_alias(family) for family, _ in chosen]}
        second = self._stage(2, [("include", self._target(family, names, tool))
                                for family, names, tool in candidates], families=chosen, prior=prior)
        inclusion = tuple(self._selected(out) for out in second)
        require(all(choice in {"yes", "no"} for choice in inclusion), "unclear_tool")
        selected = tuple(candidate for candidate, choice in zip(candidates, inclusion) if choice == "yes")
        counts = {family: sum(item[0] == family for item in selected) for family, _ in chosen}
        require(all(count > 0 for count in counts.values()), "no_verified_tool")
        require(len(selected) <= 12 and all(count <= 4 for count in counts.values()), "bounded_shortlist_exceeded")
        shortlist = [self.catalog.tool_alias(tool) for _, _, tool in selected]
        third = self._stage(3, [("verify", self._target(family, names, tool, shortlist))
                               for family, names, tool in selected], families=chosen,
                            prior={**prior, "selected_tools": shortlist})
        require(all(self._selected(out) == "yes" for out in third), "tool_verification_failed")
        return self.finish(need=need, families=tuple(family for family, _ in chosen),
                           tools=tuple(tool for _, _, tool in selected))

    def run(self):
        try:
            return self._run()
        except DecisionError as exc:
            return self.finish(fallback=exc.code)
