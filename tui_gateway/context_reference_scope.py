"""Resolve turn input references under the owning agent's existing authority."""
from agent.identity_lifecycle import bound_agent_lifecycle


@bound_agent_lifecycle
def expand_turn_references(agent, message, *, cwd, model):
    from agent.context_references import preprocess_context_references
    from agent.model_metadata import get_model_context_length

    # Turn preparation precedes run_conversation's identity binding. Metadata
    # policy checks still need the same verified identity, even for a literal @.
    context_length = get_model_context_length(
        model,
        base_url=getattr(agent, "base_url", "") or "",
        api_key=getattr(agent, "api_key", "") or "",
        provider=getattr(agent, "provider", "") or "",
        config_context_length=getattr(agent, "_config_context_length", None),
    )
    return preprocess_context_references(
        message, cwd=cwd, allowed_root=cwd, context_length=context_length)
