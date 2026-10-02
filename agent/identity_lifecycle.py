"""Trusted composition-root binding for configured per-agent authority.

Legacy profiles keep their current lifecycle. Strict profiles bind home, secrets,
terminal policy and immutable identity together before any client/tool setup.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from functools import wraps
import inspect
import json
from pathlib import Path


def _install_runtime_context(agent, context):
    # Only the trusted constructor writes the backing field. AIAgent exposes a
    # read-only property so hooks cannot accidentally replace its authority.
    object.__setattr__(agent, "_runtime_context", context)
    if not isinstance(getattr(type(agent), "runtime_context", None), property):
        agent.runtime_context = context  # lightweight construction fixtures


def identity_config():
    from hermes_cli.config_effective import load_user_config_effective
    return load_user_config_effective(fail_closed=True)


def strict_identity_enabled() -> bool:
    from agent.agent_identity import parse_agent_identity_config
    return parse_agent_identity_config(identity_config()) is not None


@contextmanager
def agent_runtime_scope(context):
    from agent.runtime_context import bind_agent_context
    if context is None:
        yield
        return
    from agent.secret_scope import (
        build_profile_secret_scope, current_secret_scope, current_secret_scope_home,
        reset_secret_scope, set_secret_scope,
    )
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.terminal_scope import install_and_reset_profile_terminal_scope

    home = Path(context.profile_home)
    # Switching homes is trusted lifecycle work. Clear the old identity only
    # while installing the new home's complete scopes, before invoking any hook.
    with ExitStack() as stack:
        stack.enter_context(bind_agent_context(None))
        home_token = set_hermes_home_override(str(home))
        stack.callback(reset_hermes_home_override, home_token)
        existing = current_secret_scope()
        stamped_home = current_secret_scope_home()
        same_home = stamped_home is not None and Path(stamped_home).resolve() == home.resolve()
        secrets = dict(existing) if existing is not None and same_home else build_profile_secret_scope(home)
        secret_token = set_secret_scope(secrets, profile_home=str(home))
        stack.callback(reset_secret_scope, secret_token)
        stack.enter_context(install_and_reset_profile_terminal_scope(home))
        stack.enter_context(bind_agent_context(context))
        yield


def bound_agent_lifecycle(function):
    @wraps(function)
    def bound(agent, *args, **kwargs):
        from agent.runtime_context import AgentContext, current_agent_context
        from agent.agent_identity import IdentityPolicyError
        context = getattr(agent, "runtime_context", None)
        context = context if isinstance(context, AgentContext) else None
        if context is None and current_agent_context() is not None:
            raise IdentityPolicyError("Unbound agent cannot run inside a configured identity scope")
        with agent_runtime_scope(context):
            return function(agent, *args, **kwargs)
    return bound


def _stored_binding(session_db, session_id):
    if session_db is None or not session_id:
        return None
    row = session_db.get_session(session_id)
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise ValueError("Session store returned an invalid identity record")
    config = row.get("model_config") or {}
    if isinstance(config, str):
        config = json.loads(config)
    if not isinstance(config, Mapping):
        raise ValueError("Session model configuration is invalid")
    if "agent_identity" in config:
        return config["agent_identity"]
    # Surfaces may precreate an empty session row before constructing AIAgent.
    # Empty-row admission is checked again atomically when claiming the binding.
    return {} if session_db.get_messages(session_id) else None


def identity_construction(function):
    signature = inspect.signature(function)

    @wraps(function)
    def construct(agent, *args, **kwargs):
        from agent.agent_identity import IdentityPolicyError, parse_agent_identity_config, resolve_agent_context
        from agent.delegation_context import is_delegated_child_process_context
        from agent.runtime_context import current_agent_context
        from hermes_constants import get_hermes_home
        from hermes_state_ids import new_session_id

        arguments = signature.bind(agent, *args, **kwargs)
        config = identity_config()
        from agent.budget_account import install_budget_policy
        install_budget_policy(agent, config, session_db=arguments.arguments.get("session_db"),
                              session_id=arguments.arguments.get("session_id"))
        parent = current_agent_context()
        if parse_agent_identity_config(config) is None:
            if parent is not None:
                raise IdentityPolicyError("Configured identity cannot downgrade to an unbound profile")
            session_db = arguments.arguments.get("session_db")
            session_id = arguments.arguments.get("session_id")
            if session_db is not None and session_id:
                row = session_db.get_session(session_id)
                model_config = row.get("model_config") if isinstance(row, Mapping) else None
                if isinstance(model_config, str):
                    model_config = json.loads(model_config)
                if isinstance(model_config, Mapping) and "agent_identity" in model_config:
                    raise IdentityPolicyError("Identity-bound session cannot resume with policy disabled")
            _install_runtime_context(agent, None)
            return function(*arguments.args, **arguments.kwargs)
        values = arguments.arguments
        session_id = values.get("session_id") or new_session_id()
        stored = _stored_binding(values.get("session_db"), session_id)
        is_child = bool(values.get("side_agent") or values.get("parent_session_id")
                        or is_delegated_child_process_context())
        identity_session_id = session_id
        if isinstance(stored, Mapping) and stored.get("session_id") != session_id:
            original = stored.get("session_id")
            db = values.get("session_db")
            # Compression continues one logical authority. Prove the existing
            # fenced lineage instead of rewriting the immutable binding or
            # trusting a copied record in an unrelated/branched session.
            if (isinstance(original, str) and db is not None
                    and db.get_session(original) is not None
                    and db._session_turn_lease_key(session_id) == db._session_turn_lease_key(original)):
                identity_session_id = original
        context = resolve_agent_context(config, session_id=identity_session_id,
                                        profile_home=str(get_hermes_home()), parent_context=parent,
                                        is_child=is_child, stored_binding=stored)
        session_db = values.get("session_db")
        if session_db is not None:
            session_db.create_session(session_id, source=values.get("platform") or "cli",
                                      parent_session_id=values.get("parent_session_id"))
            session_db.claim_session_agent_identity(session_id, context.identity.to_record())
        _install_runtime_context(agent, context)
        values["session_id"] = session_id
        # Strict memory construction is selected by the immutable identity in _init_memory.
        # The legacy background reviewer still shares its parent's store and remains disabled.
        values["skip_background_review"] = True
        with agent_runtime_scope(context):
            from agent.secret_scope import current_secret_scope
            provided_key = values.get("api_key")
            granted_values = (current_secret_scope() or {}).values()
            if provided_key and provided_key not in granted_values:
                raise IdentityPolicyError("Explicit provider credential is outside the agent secret scope")
            if values.get("credential_pool") is not None:
                raise IdentityPolicyError("External credential pools require identity-aware provider binding")
            result = function(*arguments.args, **arguments.kwargs)
            agent._session_init_model_config["agent_identity"] = context.identity.to_record()
            return result
    return construct
