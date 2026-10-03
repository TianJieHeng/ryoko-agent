"""Explicit local backend operator consumer: python -m hermes_cli.operations_cli.

No command edits credentials, network security, deployment, or model settings.
Plans print to stdout; mutation consumes a saved plan plus a separately supplied
exact digest. This interface must not be exposed as an untrusted model tool.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.operations_control import OperationsError, apply_repair, inspect_runtime, preview_repair, qualify_checkpoint_restore
from agent.operations_privacy import apply_deletion, preview_deletion, privacy_qualification, retention_inventory


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("inspect", "audit", "retention", "check-checkpoint", "preview-repair", "apply-repair", "preview-deletion", "apply-deletion"):
        child = sub.add_parser(name)
        child.add_argument("--session", required=True)
        if name.startswith("apply-"):
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--approve", required=True, help="Exact digest explicitly authorized after reviewing the plan")
        if name == "preview-repair":
            child.add_argument("--action", required=True, choices=("reconcile-effect", "retry-delivery", "revoke-lease", "rebuild-index"))
            child.add_argument("--target", required=True)
        if name == "preview-deletion":
            child.add_argument("--memory-record", default=None)
        if name == "audit":
            child.add_argument("--cursor")
            child.add_argument("--limit", type=int, default=100)
    sub.add_parser("qualification")
    child = sub.add_parser("extension-catalog")
    child.add_argument("--root", type=Path, required=True)
    child = sub.add_parser("preview-extension")
    child.add_argument("--action", required=True, choices=("pin", "revoke"))
    child.add_argument("--key", required=True)
    child.add_argument("--root", type=Path)
    child.add_argument("--manifest", type=Path)
    child = sub.add_parser("apply-extension")
    child.add_argument("--plan", type=Path, required=True)
    child.add_argument("--approve", required=True)
    return result


def _read_plan(path):
    if path.stat().st_size > 256 * 1024:
        raise OperationsError("operations_plan_too_large", "Plan exceeds its bound")
    return json.loads(path.read_text())


def _memory(context, record_id):
    if record_id is None:
        return None
    from tools.individual_memory_store import IndividualMemoryStore
    return IndividualMemoryStore(context)


def _audit(db, context, args):
    from agent.operations_audit import project_event
    from agent.operations_control import authority
    sid, _ = authority(db, context)
    page = db.replay_runtime_events(sid, cursor=args.cursor, limit=args.limit)
    return {"status": page["status"], "last_cursor": page["last_cursor"], "has_more": page["has_more"],
            "events": [project_event(event) for event in page["events"]]}


def _delete(db, context, args):
    plan = _read_plan(args.plan)
    store = _memory(context, plan.get("source", {}).get("record_id"))
    return apply_deletion(db, context, plan, authorization_digest=args.approve, memory_store=store)


def _extension_catalog(args):
    from hermes_cli.operations_extensions import catalog_metadata
    return catalog_metadata(args.root)


def _extension_preview(args):
    from hermes_cli.operations_extension_lifecycle import preview_extension_change
    return preview_extension_change(args.key, action=args.action, root=args.root,
        manifest=_read_plan(args.manifest) if args.manifest is not None else None)


def _extension_apply(args):
    from hermes_cli.operations_extension_lifecycle import apply_extension_change
    from hermes_cli.plugins import get_plugin_manager
    return apply_extension_change(_read_plan(args.plan), approval_id=args.approve, manager=get_plugin_manager())


def execute(args):
    standalone = {"qualification": lambda _: privacy_qualification(), "extension-catalog": _extension_catalog,
                  "preview-extension": _extension_preview, "apply-extension": _extension_apply}
    if args.command in standalone:
        return standalone[args.command](args)
    from agent.agent_identity import resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    home = get_hermes_home()
    readonly = not args.command.startswith("apply-")
    db = SessionDB(home / "state.db", read_only=readonly)
    try:
        binding = db.get_session_model_config_value(args.session, "agent_identity")
        context = resolve_agent_context(load_user_config_effective(fail_closed=True),
            session_id=args.session, profile_home=home, stored_binding=binding)
        with agent_runtime_scope(context):
            handlers = {
                "inspect": lambda: inspect_runtime(db, context),
                "audit": lambda: _audit(db, context, args),
                "retention": lambda: retention_inventory(db, context),
                "check-checkpoint": lambda: qualify_checkpoint_restore(db, context),
                "preview-repair": lambda: preview_repair(db, context, args.action, args.target),
                "apply-repair": lambda: apply_repair(db, context, _read_plan(args.plan), authorization_digest=args.approve),
                "preview-deletion": lambda: preview_deletion(db, context, record_id=args.memory_record,
                                                             memory_store=_memory(context, args.memory_record)),
                "apply-deletion": lambda: _delete(db, context, args),
            }
            return handlers[args.command]()
    finally:
        db.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except Exception as error:
        # Raw SQLite/config/provider errors may contain paths, statements or
        # credentials. CLI diagnostics disclose only known stable error codes.
        from hermes_state_runtime import RuntimeStoreError
        code = error.code if isinstance(error, RuntimeStoreError) else "operations_unavailable"
        print(json.dumps({"status": "rejected", "code": code}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
