"""Identity-bound execute_code adapter: exact authority, budget, then confinement."""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from pathlib import Path

from tools.environments.isolated_python import (
    IsolationTerminationUncertain, IsolationUnavailable, execute_isolated_python,
)
from tools.workspace_manifest import StagedWorkspace


def execute_strict_code(code: str, *, reset: bool = False) -> str:
    from tools.capability_broker import (
        ActionSpec, CapabilityDenied, consume_capability, issue_capability, require_live_policy, tool_action,
    )
    from tools.code_execution_tool import _load_config
    from tools.terminal_tool import _get_env_config
    from tools.interrupt import is_interrupted
    from agent.budget_account import current_budget

    context = require_live_policy()
    if context is None:
        raise CapabilityDenied("identity_required", "The isolated adapter requires a bound identity")
    if _get_env_config()["env_type"] != "local":
        return json.dumps({"status": "unsupported", "error": "executor_not_certified",
                           "message": "This backend has no certified isolated Python adapter; code was not run."})
    configured = _load_config().get("timeout", 30)
    if type(configured) not in (int, float) or not math.isfinite(configured) or configured <= 0:
        return json.dumps({"status": "denied", "error": "invalid_executor_timeout"})
    budget = current_budget()
    maximum_ms = min(32000, math.ceil((min(configured, 30) + 2) * 1000))
    if budget is not None:
        budget.check()
        maximum_ms = min(maximum_ms, budget.policy.record["request_timeout_ms"],
                         int((budget.deadline - time.time()) * 1000))
        if maximum_ms <= 2100:
            budget.block("insufficient bounded time to launch and terminate isolated Python")
    operation = budget.reserve({"executor_slots": 1, "wall_ms": maximum_ms}) if budget else None
    started = time.monotonic()
    uncertain = False
    dispatched = False
    try:
        # A new private stage per call, never a reused session kernel or parent
        # cwd. Host input mounts are intentionally not accepted by this tool API.
        stages = Path(context.profile_home) / "cache" / "isolated-code"
        stages.mkdir(parents=True, exist_ok=True, mode=0o700)
        from agent.runtime_commands import assert_runtime_dispatch
        from agent.delegation_runtime import PreparedDelegation
        run = assert_runtime_dispatch()
        prepared = getattr(run.agent, "_durable_delegation", None)
        if isinstance(prepared, PreparedDelegation):
            from agent.executor_capabilities import require_executor
            require_executor(context, prepared.handoff.to_record()["executor"], capability="python.isolated")
            prepared.workspace.verify_inputs()
            # A fresh output stage per tool invocation, with only the accepted
            # child's immutable input files. Never mount the parent's cwd/home.
            workspace = StagedWorkspace.create(stages / uuid.uuid4().hex,
                parent_root=prepared.workspace.inputs,
                input_paths=tuple(item.path for item in prepared.workspace.manifest.inputs),
                base_revision=prepared.handoff.sha256)
        else:
            workspace = StagedWorkspace.create(stages / uuid.uuid4().hex)
        args = {"code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "workspace_manifest_digest": workspace.manifest.digest,
                "wall_ms": maximum_ms, "reset": bool(reset)}
        base = tool_action("execute_code", args)
        spec = ActionSpec(base.name, args, base.operation_class, resource_roots=(workspace.root,),
                          contract_digest=base.contract_digest)
        capability = issue_capability(spec)
        consume_capability(capability, spec)
        if budget:
            budget.dispatched(operation)
            dispatched = True
        remaining = maximum_ms / 1000 - (time.monotonic() - started)
        if remaining <= 2:
            raise IsolationUnavailable("executor launch allowance exhausted before execution")
        result = execute_isolated_python(code, workspace=workspace, timeout_seconds=min(30, remaining - 2),
                                         wall_seconds=remaining, is_cancelled=is_interrupted)
        return json.dumps(result)
    except IsolationTerminationUncertain:
        uncertain = True
        return json.dumps({"status": "outcome_uncertain", "error": "executor_termination_unacknowledged",
                           "message": "Local execution has not acknowledged termination; its budget slot remains held."})
    except IsolationUnavailable:
        return json.dumps({"status": "unsupported", "error": "executor_enforcement_unavailable",
                           "message": "Required OS confinement is unavailable; unsafe fallback is disabled."})
    finally:
        if budget is not None:
            if not dispatched:
                budget.db.release_budget_reservation(budget.account_id, budget.actor, operation, **budget.fence)
            else:
                budget.settle(operation, {"wall_ms": math.ceil((time.monotonic() - started) * 1000)},
                              unknown=uncertain, slots_released=not uncertain)
