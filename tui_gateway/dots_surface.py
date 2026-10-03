"""Dots tool exposure is frozen by the authenticated stdio construction surface."""
from contextvars import ContextVar
from dataclasses import dataclass
import threading

from .transport import StdioTransport

DOTS_TOOLS = frozenset({"dots_page_read", "dots_page_propose", "dots_computer_observe", "dots_computer_propose"})
_ADVERTISED = set()
_LOCK = threading.Lock()
_CONSTRUCTION = ContextVar("dots_native_construction", default=None)


@dataclass
class _Construction:
    transport: object
    active: bool = True


def advertise_native_surface(transport, enabled):
    # Remote renderers cannot turn their generic peer requests into native
    # execution. Changes affect only future agent construction.
    with _LOCK:
        if enabled and type(transport) is StdioTransport:
            _ADVERTISED.add(transport)
        else:
            _ADVERTISED.discard(transport)


def construct_native_agent(constructor, *, dots_transport, **kwargs):
    with _LOCK:
        native = type(dots_transport) is StdioTransport and dots_transport in _ADVERTISED
    transport = dots_transport if native else None
    selected = kwargs.get("enabled_toolsets")
    if native and selected is not None and "dots_native" not in (kwargs.get("disabled_toolsets") or []):
        kwargs["enabled_toolsets"] = [*selected, *([] if "dots_native" in selected else ["dots_native"])]
    construction = _Construction(transport) if transport is not None else None
    token = _CONSTRUCTION.set(construction)
    try:
        agent = constructor(**kwargs)
    finally:
        if construction is not None:
            construction.active = False
        _CONSTRUCTION.reset(token)
    # Written once, after construction; changing advertisement or registration
    # never mutates an existing schema snapshot or cached prompt prefix.
    agent._dots_native_transport = transport
    return agent


def surface_tool_allowed(context):
    if context is None:
        return False
    construction = _CONSTRUCTION.get()
    if construction is not None and construction.active:
        return True
    from agent.runtime_commands import _RUN
    run = _RUN.get()
    return bool(run is not None and run.context == context
                and type(getattr(run.agent, "_dots_native_transport", None)) is StdioTransport)


def require_registered_surface(run, registration):
    from tools.capability_broker import CapabilityDenied
    if (not surface_tool_allowed(run.context)
            or getattr(run.agent, "_dots_native_transport", None) is not registration.transport):
        raise CapabilityDenied("dots_surface_required", "Native tools require their original registered stdio surface; start a newly advertised session")
