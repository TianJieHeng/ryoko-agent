"""Fail legacy strict cron closed before scripts, source reads or client setup."""
from contextlib import contextmanager


@contextmanager
def job_identity_scope(job):
    from agent.agent_identity import IdentityPolicyError, parse_agent_identity_config, resolve_owned_agent_context
    from agent.identity_lifecycle import agent_runtime_scope, identity_config
    from hermes_constants import get_hermes_home
    config = identity_config()
    parsed = parse_agent_identity_config(config)
    if parsed is None:
        if job.get("owner_binding") is not None:
            raise IdentityPolicyError("Identity-owned cron cannot downgrade to legacy execution")
        yield
        return
    owner = job.get("owner_binding")
    if not isinstance(owner, dict):
        raise IdentityPolicyError("Strict cron requires a persisted owner; import paused and reauthorize")
    context = resolve_owned_agent_context(config, owner_binding=owner,
        session_id="cron_guard", profile_home=get_hermes_home())
    with agent_runtime_scope(context):
        # The old agent/script/URL paths have no durable bounded adapter. This
        # check is deliberately before _prepare_job_prompt, which can do I/O.
        raise IdentityPolicyError("Strict cron agent, script and URL jobs are unsupported; use a bounded durable schedule")
        yield  # pragma: no cover
