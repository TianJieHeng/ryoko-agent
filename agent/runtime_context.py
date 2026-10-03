"""Immutable identity scope carried with the existing turn payload and thread context.

This contains authority metadata only: never an AIAgent, expanded configuration,
credential dictionary or catch-all service locator. Existing provider/tool/store
owners retain their concrete APIs until a consumer needs a narrow adapter.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from agent.agent_identity import AgentPolicy, IdentityBinding, IdentityPolicyError


def canonical_profile_home(home: str | Path) -> str:
    if not isinstance(home, (str, Path)) or not str(home).strip():
        raise IdentityPolicyError("profile_home must be a nonempty path")
    # Do not cache: a replaced symlink must not retain the authority of its old target.
    return os.path.normcase(str(Path(home).expanduser().resolve(strict=False)))


@dataclass(frozen=True)
class AgentContext:
    identity: IdentityBinding
    policy: AgentPolicy
    config_digest: str
    profile_home: str = field(repr=False)
    configuration_session_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from agent.agent_identity import _digest, _identifier

        if self.configuration_session_id is not None:
            _identifier(self.configuration_session_id, "configuration_session_id")
        object.__setattr__(
            self, "profile_home", canonical_profile_home(self.profile_home)
        )
        if (
            self.identity.config_digest != self.config_digest
            or self.identity.policy_digest != self.policy.digest
        ):
            raise IdentityPolicyError(
                "context policy/configuration does not match its identity binding"
            )
        if self.identity.profile_home_digest != _digest(self.profile_home):
            raise IdentityPolicyError(
                "context home does not match its identity binding"
            )
        if (self.identity.lifecycle == "ephemeral") != (self.policy.role == "child"):
            raise IdentityPolicyError(
                "context lifecycle does not match its policy role"
            )

    def validate_profile_home(self) -> None:
        from hermes_constants import get_hermes_home

        if canonical_profile_home(get_hermes_home()) != self.profile_home:
            raise IdentityPolicyError(
                "active profile home does not match the bound agent identity"
            )

    def redacted_inspection(self) -> dict:
        return {
            "identity": self.identity.to_record(),
            "policy": self.policy.to_record(redacted=True),
            "config_digest": self.config_digest,
            "provenance": {
                "source": "effective_config.agent_identity",
                "schema_version": 1,
                "profile_home": "[redacted]",
            },
        }


_AGENT_CONTEXT: ContextVar[AgentContext | None] = ContextVar(
    "agent_runtime_context", default=None
)


def current_agent_context() -> AgentContext | None:
    context = _AGENT_CONTEXT.get()
    if context is not None:
        context.validate_profile_home()
    return context


def set_agent_context(context: AgentContext | None) -> Token:
    if context is not None:
        if not isinstance(context, AgentContext):
            raise IdentityPolicyError("agent context must be a validated AgentContext")
        context.validate_profile_home()
    return _AGENT_CONTEXT.set(context)


def reset_agent_context(token: Token) -> None:
    _AGENT_CONTEXT.reset(token)


@contextmanager
def bind_agent_context(context: AgentContext | None) -> Iterator[AgentContext | None]:
    token = set_agent_context(context)
    try:
        yield context
    finally:
        reset_agent_context(token)
