"""Legacy loop test fixtures for the paired schema/exposure construction API."""

from agent.runtime_context import current_agent_context
from agent.tool_view import ToolView


def tool_definitions_with_view(definitions):
    """Keep an explicit fake tool catalog coherent without probing real services."""
    names = tuple(definition["function"]["name"] for definition in definitions)
    context = current_agent_context()
    policy = context.policy.digest if context is not None else "legacy"
    return definitions, ToolView("fixture", policy, names, names, names, names, {})
