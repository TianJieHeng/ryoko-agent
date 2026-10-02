"""Read-only redacted identity inspection for the existing config command."""
from __future__ import annotations

import json
import sqlite3

from agent.agent_identity import IdentityBinding, IdentityPolicyError, parse_agent_identity_config
from hermes_cli.config_effective import load_user_config_effective
from hermes_constants import get_hermes_home


def inspect_identity(*, agent_id: str | None = None, session_id: str | None = None) -> dict:
    parsed = parse_agent_identity_config(load_user_config_effective(fail_closed=True))
    if parsed is None:
        return {"status": "legacy", "identity_policy": "not configured"}
    selected = agent_id or parsed.active_agent_id
    binding = None
    if session_id is not None:
        db_path = get_hermes_home() / "state.db"
        if not db_path.is_file():
            raise IdentityPolicyError("Session store is unavailable")
        # Inspect never initializes/migrates the store or opens a provider.
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT model_config FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise IdentityPolicyError("Session was not found in the selected profile")
        stored = json.loads(row[0] or "{}")
        binding = IdentityBinding.from_record(stored.get("agent_identity", {}))
        if agent_id is not None and agent_id != binding.agent_id:
            raise IdentityPolicyError("Selected agent does not own this session")
        selected = binding.agent_id
    policy = parsed.agents.get(selected)
    if policy is None:
        raise IdentityPolicyError("Selected stable agent is not configured; ephemeral inspection requires its parent")
    current = binding is None or (binding.config_digest == parsed.digest and binding.policy_digest == policy.digest)
    return {
        "status": "configured" if current else "stale_binding",
        "schema_version": parsed.schema_version,
        "principal_id": parsed.principal_id,
        "profile_id": parsed.profile_id,
        "agent_id": selected,
        "config_digest": parsed.digest,
        "policy": policy.to_record(redacted=True),
        "session_binding": binding.to_record() if binding is not None else None,
        "provenance": {"loader": "effective profile configuration", "layers": ["user", "managed overlay"]},
        "memory_status": "unavailable until the per-agent memory router is configured",
        "restart_policy": "new sessions only; stale stored bindings require explicit migration",
    }


def config_identity_command(args) -> None:
    print(json.dumps(inspect_identity(agent_id=getattr(args, "agent", None),
                                     session_id=getattr(args, "session", None)), indent=2))
