"""DP16 causal planning and immutable schema bundles with an authorized escape.

Plans are hints. Live discovery and dispatch retain the existing policy checks.
No-tools still exposes search/describe/call. Reopening returns schemas *as data*
through that bridge, never swaps the conversation's frozen schema prefix.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
import json
import time

from agent.decisions.contracts import canonical, digest, require, sha256
from agent.decisions.batching import PlannerMetrics
from agent.decisions.registry import contract_for
from agent.decisions.state import build_state

BRIDGES = ("tool_search", "tool_describe", "tool_call")


@dataclass(frozen=True)
class LiveCatalog:
    version: str
    scope_digest: str
    definitions_json: str = field(repr=False)
    families: tuple[tuple[str, tuple[str, ...]], ...]
    bridge_names: tuple[str, ...]
    tool_descriptors: tuple
    family_descriptors: tuple
    policy_digest: str
    tool_view_revision: str

    def __post_init__(self):
        from agent.decisions.planner_catalog import validate_snapshot, version_material
        for value in (self.version, self.scope_digest, self.policy_digest, self.tool_view_revision):
            sha256(value)
        require(all(type(value) is tuple for value in (self.families, self.bridge_names,
                    self.tool_descriptors, self.family_descriptors)), "invalid_catalog")
        require(digest(version_material(vars(self))) == self.version, "catalog_version_mismatch")
        validate_snapshot(vars(self), bridges=BRIDGES)

    @property
    def definitions(self):
        return json.loads(self.definitions_json)

    @property
    def tool_ids(self):
        return tuple(item.tool_id for item in self.tool_descriptors)

    @property
    def descriptor_values(self):
        """Fresh JSON-safe data for the renderer; no raw schemas or source IDs."""
        from agent.decisions.planner_catalog import descriptor_values
        return descriptor_values(vars(self))

    def tool_alias(self, tool_id):
        return self._lookup(self.tool_descriptors, "tool_id", tool_id, "alias")

    def family_alias(self, family_id):
        return self._lookup(self.family_descriptors, "family_id", family_id, "alias")

    def resolve_tool_alias(self, alias):
        return self._lookup(self.tool_descriptors, "alias", alias, "tool_id")

    def resolve_family_alias(self, alias):
        return self._lookup(self.family_descriptors, "alias", alias, "family_id")

    @staticmethod
    def _lookup(descriptors, key, value, result):
        found = [getattr(item, result) for item in descriptors if getattr(item, key) == value]
        require(len(found) == 1, "unknown_catalog_alias")
        return found[0]

    @classmethod
    def from_authorized(cls, definitions, *, scope_digest, families, bridge_names,
                        policy_digest=None, tool_view_revision=None, family_descriptions=None,
                        sources=None, effects=None):
        from agent.decisions.planner_catalog import snapshot_fields
        return cls(**snapshot_fields(definitions, scope_digest=scope_digest, families=families,
            bridge_names=bridge_names, bridges=BRIDGES, policy_digest=policy_digest,
            tool_view_revision=tool_view_revision, family_descriptions=family_descriptions,
            sources=sources, effects=effects))


def live_catalog(definitions, *, scope_digest):
    """Recheck ownership and session visibility before inspecting descriptions."""
    from tools.capability_broker import require_live_policy
    from tools.agent_policy_gate import authorize_tool
    from tools.registry import registry
    from agent.runtime_commands import assert_runtime_dispatch
    from agent.decisions.receipts import scope_digest as owner_digest
    from agent.decisions.planner_catalog import host_metadata
    context = require_live_policy()
    require(context is not None and owner_digest(context) == scope_digest, "planner_owner_mismatch")
    run = assert_runtime_dispatch()
    view = getattr(run.agent, "tool_view", None)
    if view is not None:
        require(view.session_policy_version == context.policy.digest, "planner_owner_mismatch")
    admitted = []
    for item in definitions:
        require(isinstance(item, dict) and isinstance(item.get("function"), dict), "invalid_catalog_schema")
        name = item["function"].get("name")
        require(isinstance(name, str), "invalid_catalog_id")
        if authorize_tool(name, context=context) is not None:
            continue
        if view is not None and name not in view.discoverable_tool_ids and name not in BRIDGES:
            continue
        admitted.append(item)
    regular = [item for item in admitted if item["function"]["name"] not in BRIDGES]
    metadata = host_metadata(regular, context, registry=registry, scope_digest=scope_digest)
    bridges = tuple(name for name in BRIDGES if authorize_tool(name, context=context) is None)
    revision = None if view is None else digest({"catalog": view.catalog_version,
        "policy": view.session_policy_version, "discoverable": sorted(view.discoverable_tool_ids),
        "selected": sorted(view.selected_tool_ids)})
    # Recheck the real owner after snapshot assembly too. The result remains a
    # hint: selection/installation/recovery/dispatch still authorize separately.
    catalog = LiveCatalog.from_authorized(admitted, scope_digest=scope_digest, bridge_names=bridges,
        policy_digest=context.policy.digest, tool_view_revision=revision, **metadata)
    require(require_live_policy() == context, "planner_owner_mismatch")
    return catalog


@dataclass(frozen=True)
class ToolPlan:
    need: str
    effort_bucket: str
    families: tuple[str, ...]
    verified_tool_ids: tuple[str, ...]
    live_catalog_version: str
    bundle_id: str
    reopen_policy: str
    scope_digest: str
    mode: str
    fallback: str | None
    decision_receipt_ids: tuple[str, ...]
    elapsed_ms: float
    protocol_version: int = 1
    metrics: PlannerMetrics = field(default_factory=PlannerMetrics)

    def to_record(self):
        return asdict(self)


class ToolPlanner:
    def __init__(self, client, *, catalog_lookup, fence=None):
        self.client, self.catalog_lookup, self.fence = client, catalog_lookup, fence

    def plan(self, *, request, scope_digest, deadline, goal="", context="", classification="private",
             planner_context=None):
        if self.client.native_batches:
            from agent.decisions.planner_v2 import NativeToolPlanner
            return NativeToolPlanner(self, request=request, scope_digest=scope_digest, deadline=deadline,
                goal=goal, context=context, classification=classification, planner_context=planner_context).run()
        started = time.monotonic()
        catalog = self.catalog_lookup()
        require(catalog.scope_digest == scope_digest, "planner_owner_mismatch")
        policy = self.client.policy("DP16")
        observations = []

        def finish(need="defer", effort="defer", families=(), verified=(), fallback=None, selected_catalog=None):
            current = selected_catalog or catalog
            material = {"need": need, "effort_bucket": effort, "families": families,
                        "verified_tool_ids": verified, "live_catalog_version": current.version,
                        "scope_digest": scope_digest}
            return ToolPlan(need, effort, tuple(families), tuple(verified), current.version,
                digest(material), "authorized_search_describe_call", scope_digest, policy.mode, fallback,
                tuple(out.receipt["receipt_id"] for out in observations if out.receipt),
                (time.monotonic() - started) * 1000)

        if policy.mode == "off":
            return finish(fallback="off")
        if set(catalog.bridge_names) != set(BRIDGES):
            return finish(fallback="authorized_bridge_required")
        if len(catalog.families) > 16 or any(len(names) > 62 for _, names in catalog.families):
            return finish(fallback="bounded_menu_exceeded")
        base = {"request": request, "goal": goal, "context": context,
                "catalog_version": catalog.version}

        def ask(question, values, options=None):
            packet = build_state("DP16", {**base, **values}, scope_digest=scope_digest, classification=classification)
            return self.client.decide("DP16", packet, contract_for("DP16").version, deadline,
                                      question_id=question, live_options=options)

        def batch(jobs):
            # A logical batch of independent closed questions. The BE15 wire
            # protocol remains one question per request, bounded by client slots.
            # Conditional requests cannot start before this stage finishes.
            with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
                futures = [pool.submit(copy_context().run, ask, question, values, options)
                           for question, values, options in jobs]
                outcomes = [future.result() for future in futures]
            observations.extend(outcomes)
            return outcomes

        def selected(out):
            receipt = out.receipt or {}
            choice = receipt.get("selected")
            confidence = (receipt.get("distribution") or {}).get(choice, 0)
            if (out.fallback not in {None, "shadow_observation"} or choice in {None, "unclear"}
                    or confidence < policy.threshold_for(choice)):
                return None
            return choice

        jobs = [("need", {}, None), ("effort", {}, None)] + [
            ("family", {"selected_family": family, "menu": list(names)}, None)
            for family, names in catalog.families]
        stage_one = batch(jobs)
        need, effort = selected(stage_one[0]), selected(stage_one[1])
        if need not in {"no_tools", "needs_tools"}:
            return finish(effort=effort or "defer", fallback=stage_one[0].fallback or "unclear_need")
        current = self.catalog_lookup()
        require(current.scope_digest == scope_digest, "planner_owner_mismatch")
        if current.version != catalog.version:
            return finish(fallback="stale_catalog", selected_catalog=current)
        if any(selected(out) is None for out in stage_one[2:]):
            return finish(effort=effort or "defer", fallback="unclear_family")
        if need == "no_tools":
            if any(selected(out) == "yes" for out in stage_one[2:]):
                return finish(effort=effort or "defer", fallback="contradictory_tool_need")
            return finish(need, effort or "defer")
        chosen = [(family, names) for (family, names), out in zip(catalog.families, stage_one[2:]) if selected(out) == "yes"]
        if not chosen:
            return finish(effort=effort or "defer", fallback="no_verified_family")
        tool_choices = batch([("tool", {"selected_family": family, "menu": list(names)},
                               tuple(names) + ("none", "unclear")) for family, names in chosen])
        candidates = [(family, names, selected(out)) for (family, names), out in zip(chosen, tool_choices)
                      if selected(out) in names]
        if len(candidates) != len(chosen):
            return finish(effort=effort or "defer", fallback="no_verified_tool")
        verifications = batch([("verify", {"selected_family": family, "selected_tool": name,
                                          "menu": list(names)}, None) for family, names, name in candidates])
        current = self.catalog_lookup()
        require(current.scope_digest == scope_digest, "planner_owner_mismatch")
        if current.version != catalog.version:
            return finish(fallback="stale_catalog", selected_catalog=current)
        verified = tuple(name for (_, _, name), out in zip(candidates, verifications) if selected(out) == "yes")
        if len(verified) != len(candidates):
            return finish(effort=effort or "defer", fallback="tool_verification_failed")
        return finish(need, effort or "defer", tuple(family for family, _ in chosen), verified)


@dataclass(frozen=True)
class FrozenBundle:
    context_id: str
    scope_digest: str
    catalog_version: str
    schemas_json: str
    plan_bundle_id: str | None
    selected_tool_ids: tuple[str, ...]

    @property
    def schemas(self):
        return json.loads(self.schemas_json)

    @property
    def prefix_digest(self):
        return digest(self.schemas_json)


class BundleSession:
    """The only mutating operation requires a new-context/compression boundary.

    The production observer does not call install. An enforcing owner must supply
    a PointResolution from its qualified policy book and honor the same boundary
    as the real prompt compressor; a new task ID alone is not such a boundary.
    """
    def __init__(self):
        self.bundle = None

    def install(self, *, catalog, incumbent_schemas, context_id, boundary, plan=None, resolution=None):
        require(boundary in {"new_context", "compression", "same_context"}, "invalid_cache_boundary")
        if self.bundle is not None:
            require(catalog.scope_digest == self.bundle.scope_digest, "planner_owner_mismatch")
            if boundary == "same_context":
                require(context_id == self.bundle.context_id, "new_context_boundary_required")
                return self.bundle
            if boundary == "new_context":
                require(context_id != self.bundle.context_id, "new_context_boundary_required")
        else:
            require(boundary == "new_context", "new_context_boundary_required")
        allowed = set(catalog.tool_ids) | set(catalog.bridge_names)
        schemas = [item for item in incumbent_schemas if item["function"]["name"] in allowed]
        selected_plan = None
        use_plan = (plan is not None and resolution is not None and resolution.point_id == "DP16"
                    and resolution.mode == "enforce" and resolution.effective.action == "bundle"
                    and resolution.gate_digest is not None and resolution.scope_digest == catalog.scope_digest
                    and plan.mode == "enforce" and plan.fallback is None
                    and plan.scope_digest == catalog.scope_digest and plan.live_catalog_version == catalog.version
                    and set(catalog.bridge_names) == set(BRIDGES))
        if use_plan:
            desired = set(resolution.effective.value)
            require(desired <= set(plan.verified_tool_ids) and desired <= set(catalog.tool_ids), "unverified_bundle_tool")
            from tools.tool_search import bridge_tool_schemas
            schemas = [item for item in catalog.definitions if item["function"]["name"] in desired]
            schemas += bridge_tool_schemas(len(catalog.tool_ids))
            selected_plan = plan.bundle_id
        self.bundle = FrozenBundle(context_id, catalog.scope_digest, catalog.version, canonical(schemas),
            selected_plan, tuple(item["function"]["name"] for item in schemas))
        return self.bundle

    def reopen(self, tool_id, *, catalog_lookup, receipt_sink):
        """Describe an authorized miss as tool-result data. Invocation stays brokered."""
        require(self.bundle is not None, "bundle_required")
        catalog = catalog_lookup()
        require(catalog.scope_digest == self.bundle.scope_digest, "planner_owner_mismatch")
        # A stale snapshot must never authorize the old tool. Reopening obtains a
        # fresh view and current allowed schemas; denied names are not echoed.
        permitted = set(catalog.bridge_names) == set(BRIDGES) and tool_id in catalog.tool_ids
        record = {"kind": "planner_miss", "scope_digest": catalog.scope_digest,
                  "bundle_id": self.bundle.plan_bundle_id, "catalog_version": catalog.version,
                  "previous_catalog_version": self.bundle.catalog_version,
                  "tool_digest": digest(tool_id), "recovered": permitted,
                  "reason": "authorized_reopen" if permitted else "not_authorized_or_unavailable",
                  "prefix_digest": self.bundle.prefix_digest}
        receipt_sink(record)
        require(permitted, "not_authorized_or_unavailable")
        return next(item for item in catalog.definitions if item["function"]["name"] == tool_id)
