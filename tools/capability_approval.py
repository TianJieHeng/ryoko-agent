"""Exact BE05 approval contracts rendered through the existing human surfaces.

No smart classifier, yolo setting, session cache or unavailable UI can grant an
exact approval. Broad answers from older clients remain fail-closed.
"""
from __future__ import annotations

import json
import sys

from tools.capability_broker import CapabilityDenied, preview_action, resolve_approval


def request_exact_approval(action):
    from agent.redact import redact_sensitive_text
    from tools import approval, approval_context
    from tools.approval_gateway_wait import _await_gateway_decision
    from tools.approval_prompt import _present_with_selected_transport, prompt_dangerous_approval

    preview = preview_action(action)
    display = redact_sensitive_text(json.dumps(action.to_record(), sort_keys=True, ensure_ascii=True))
    description = (f"Approve this exact {action.operation_class} action once. "
                   f"Action digest: {action.digest}; policy version: {preview.authority.policy_version}")
    key = f"exact:{preview.approval_id}:{preview.approval_digest}"
    callback, is_cli, is_gateway, is_ask = approval._presence()
    session_key = approval_context.get_current_session_key()
    if not (is_cli or is_gateway or is_ask):
        raise CapabilityDenied("approval_surface_unavailable", "Exact approval is pending because no human approval surface is available", pending=True)
    attempt = _present_with_selected_transport(command=display, description=description, pattern_key=key,
        pattern_keys=[key], session_key=session_key, surface="gateway" if is_gateway or is_ask else "cli",
        allow_session=False, allow_permanent=False)
    if attempt.get("failure") and attempt.get("fallback") == "builtin":
        attempt = {"selected": False}
    if attempt.get("selected"):
        if attempt.get("failure"):
            raise CapabilityDenied("approval_surface_unavailable", "The selected approval surface is unavailable", pending=True)
        choice = attempt.get("choice")
    else:
        notify = approval._gateway_notify_cb(session_key) if is_gateway or is_ask else None
        if notify is not None:
            decision = _await_gateway_decision(session_key, notify, {
                "command": display, "description": description, "pattern_key": key, "pattern_keys": [key],
                "allow_session": False, "allow_permanent": False, "approval_id": preview.approval_id,
                "approval_digest": preview.approval_digest, "expires_at": preview.expires_at,
            })
            choice = decision.get("choice") if decision.get("resolved") and not decision.get("notify_failed") else None
        elif is_cli and (callback is not None or sys.stdin.isatty()):
            choice = prompt_dangerous_approval(display, description, timeout_seconds=300,
                approval_callback=callback, allow_session=False, allow_permanent=False, title="Approve exact action")
        else:
            raise CapabilityDenied("approval_surface_unavailable", "Exact approval is pending because its human surface is unavailable", pending=True)
    if choice != "once":
        resolve_approval(preview, preview.approval_digest, "deny")
        raise CapabilityDenied("exact_approval_denied", "The exact action was not approved once", pending=choice in (None, "timeout", "cancelled"))
    resolve_approval(preview, preview.approval_digest, choice)
    return preview
