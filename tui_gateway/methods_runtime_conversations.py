"""Canonical conversation ingress for the server-owned single-owner stdio pipe.

This deliberately does not widen WebSocket login authority or legacy session APIs.
A BFF must map its verified human owner to this isolated configured producer.
"""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _conversation_rpc(name, model_name):
    def decorate(function):
        def handler(rid, params):
            import sqlite3
            from hermes_cli.memory_sessions import ConversationError
            from agent.agent_identity import IdentityPolicyError, resolve_agent_context
            from agent.agent_configuration import base_configuration
            from agent.runtime_commands import _RUN
            from hermes_state_runtime import RuntimeStoreError
            from tui_gateway.contracts import runtime_conversations as models
            from tui_gateway.transport import StdioTransport

            request, error = _runtime_validate(rid, params, getattr(models, model_name))
            if error:
                return error
            peer = current_transport() or _stdio_transport
            if peer is not _stdio_transport or not isinstance(peer, StdioTransport):
                return _err(rid, 5010, "Canonical conversations require the trusted stdio owner",
                            {"code": "runtime_transport_unsupported"})
            if _RUN.get() is not None:
                return _err(rid, 4030, "Conversation ingress requires its human owner",
                            {"code": "conversation_owner_required"})
            # Never let a caller choose the profile or inherit an unrelated worker context.
            with _profile_build_scope(_hermes_home):
                try:
                    config = base_configuration()
                    context = resolve_agent_context(config, session_id="conversation_ingress", profile_home=_hermes_home)
                    if context is None:
                        return _err(rid, 4030, "Configured stable identity required", {"code": "identity_required"})
                    db = _get_db()
                    if db is None:
                        return _err(rid, 5030, "Durable conversation store unavailable", {"code": "durable_store_required"})
                    return _ok(rid, function(rid, request, context, config, db))
                except ConversationError as exc:
                    return _err(rid, 4090, str(exc), {"code": "memory_conversation_unavailable"})
                except PermissionError:
                    return _err(rid, 4030, "Memory conversation owner does not match", {"code": "identity_mismatch"})
                except IdentityPolicyError:
                    return _err(rid, 4030, "Configured identity does not match the conversation", {"code": "identity_mismatch"})
                except RuntimeStoreError as exc:
                    return _runtime_store_error(rid, exc)
                except (sqlite3.Error, OSError):
                    return _err(rid, 5030, "Durable conversation operation unavailable", {"code": "durable_store_required"})
        return method(name)(handler)
    return decorate


@_conversation_rpc("runtime.conversation.capabilities", "RuntimeConversationParams")
def _conversation_capabilities(rid, request, context, config, db):
    from hermes_state_conversations import CONVERSATION_MAX_PAGE, CONVERSATION_TEXT_CHARS, CONVERSATION_TEXT_BYTES
    identity = context.identity
    return {"schema_version": 1, "authority": "trusted_stdio_owner",
            "owner_scope": "principal_profile_agent_home",
            "identity": {"principal_id": identity.principal_id, "profile_id": identity.profile_id,
                         "agent_id": identity.agent_id, "policy_digest": identity.policy_digest,
                         "config_digest": identity.config_digest, "role": context.policy.role,
                         "memory_backend": context.policy.memory_backend},
            "methods": ["runtime.conversation." + name for name in
                        ("capabilities", "create", "list", "bind", "rename", "archive", "history", "export", "operation.get", "command.receipt")]
                       + ["runtime.command.receipt", "runtime.memory.list", "runtime.memory.open"],
            "max_page": CONVERSATION_MAX_PAGE, "max_text_chunk_chars": CONVERSATION_TEXT_CHARS,
            "max_page_text_bytes": CONVERSATION_TEXT_BYTES, "transcript_format": "safe_transcript_v1",
            "command_message_linkage": "explicit", "restore_supported": False}


@_conversation_rpc("runtime.conversation.create", "RuntimeConversationCreateParams")
def _conversation_create(rid, request, context, config, db):
    from agent.conversation_identity import selected_conversation_scope
    selected, enrollment = selected_conversation_scope(context, config, db, request.agent_id, session_id=_new_session_key())
    return db.create_runtime_conversation(selected, request.model_dump(exclude_none=True), configuration_enrollment=enrollment)


@_conversation_rpc("runtime.conversation.list", "RuntimeConversationListParams")
def _conversation_list(rid, request, context, config, db):
    from agent.conversation_identity import selected_conversation_scope
    selected, _ = selected_conversation_scope(context, config, db, request.agent_id, session_id="conversation_ingress")
    return db.list_runtime_conversations(selected, **request.model_dump(exclude={"schema_version", "agent_id"}))


def _conversation_binding_state(session, context, db):
    from agent.runtime_context import AgentContext
    if session.get("agent_error"):
        return "failed", "agent_build_failed"
    agent = session.get("agent")
    if agent is None:
        return "building", None
    actual = getattr(agent, "runtime_context", None)
    if (not isinstance(actual, AgentContext) or actual != context
            or getattr(agent, "_session_db", None) is not db):
        return "failed", "identity_mismatch"
    return "ready", None


@_conversation_rpc("runtime.conversation.bind", "RuntimeConversationRefParams")
def _conversation_bind(rid, request, context, config, db):
    from agent.conversation_identity import recorded_conversation_scope
    from hermes_state_runtime import RuntimeStoreError

    # Prove the owner/home/profile before resolving the stored selected identity.
    expected, _ = recorded_conversation_scope(context, config, db, request.conversation_id)
    owned = db.read_runtime_conversation(expected, request.conversation_id)
    tip = owned["lineage"][-1]
    ctx = _Resume(rid, {"source": "web", "omit_messages": True, "inline_images": False}, tip)
    ctx.db, ctx.owns_db, ctx.found = db, False, db.get_session(tip)
    if _resume_guard(ctx) is not None:
        raise RuntimeStoreError("resume_limit", "Conversation exceeds the configured resume limit")
    ctx.profile_resume_cwd = _str_param(ctx.found, "cwd") or _profile_workspace_cwd(ctx.profile_home)
    with _session_resume_lock:
        live = _find_live_session_by_key(tip, ctx.profile_home)
        if live is not None:
            sid, session = live
            if session.get("transport") is not _stdio_transport:
                raise RuntimeStoreError("identity_mismatch", "Live conversation belongs to another transport")
            readiness, failure = _conversation_binding_state(session, expected, db)
            return {"schema_version": 1, "conversation": owned["conversation"], "session_id": sid,
                    "readiness": readiness, "failure_code": failure}
    history, _display, _raw = ctx.restore()
    overrides = _stored_session_runtime_overrides(ctx.found)
    # This ingress already proved the single-owner stdio peer. Its agent replies
    # on the existing local TUI surface; the durable conversation's web creation
    # provenance does not make this an external web/gateway delivery runtime.
    record = ctx.record("tui", ctx.profile_resume_cwd or _default_session_cwd(), history, overrides,
                        display_history_prefix=ctx.display_prefix(), todo_state=_todo_state_from_history(history))
    new_session = False
    with _session_resume_lock:
        live = _find_live_session_by_key(tip, ctx.profile_home)
        if live is not None:
            sid, session = live
            if session.get("transport") is not _stdio_transport:
                raise RuntimeStoreError("identity_mismatch", "Live conversation belongs to another transport")
        else:
            sid, session = uuid.uuid4().hex[:8], record
            with _sessions_lock:
                _sessions[sid] = session
            _register_session_cwd(session)
            new_session = True
        readiness, failure = _conversation_binding_state(session, expected, db)
    if new_session:
        _schedule_agent_build(sid)
        _schedule_session_cap_enforcement()
    return {"schema_version": 1, "conversation": owned["conversation"], "session_id": sid,
            "readiness": readiness, "failure_code": failure}


@_conversation_rpc("runtime.conversation.rename", "RuntimeConversationRenameParams")
def _conversation_rename(rid, request, context, config, db):
    from agent.conversation_identity import recorded_conversation_scope
    selected, _ = recorded_conversation_scope(context, config, db, request.conversation_id)
    return db.mutate_runtime_conversation(selected, request.model_dump(), operation="rename")


@_conversation_rpc("runtime.conversation.archive", "RuntimeConversationArchiveParams")
def _conversation_archive(rid, request, context, config, db):
    from agent.conversation_identity import recorded_conversation_scope
    selected, _ = recorded_conversation_scope(context, config, db, request.conversation_id)
    return db.mutate_runtime_conversation(selected, request.model_dump(), operation="archive")


@_conversation_rpc("runtime.conversation.history", "RuntimeConversationHistoryParams")
def _conversation_history(rid, request, context, config, db):
    from agent.conversation_identity import recorded_conversation_scope
    selected, _ = recorded_conversation_scope(context, config, db, request.conversation_id)
    return db.read_runtime_conversation_history(selected, request.conversation_id, limit=request.limit, cursor=request.cursor)


@_conversation_rpc("runtime.conversation.export", "RuntimeConversationHistoryParams")
def _conversation_export(rid, request, context, config, db):
    from agent.conversation_identity import recorded_conversation_scope
    selected, _ = recorded_conversation_scope(context, config, db, request.conversation_id)
    return db.read_runtime_conversation_history(selected, request.conversation_id, limit=request.limit, cursor=request.cursor)


@_conversation_rpc("runtime.conversation.operation.get", "RuntimeConversationOperationParams")
def _conversation_operation_get(rid, request, context, config, db):
    from agent.conversation_identity import selected_conversation_scope
    selected, _ = selected_conversation_scope(context, config, db, request.agent_id, session_id="conversation_ingress")
    return db.read_runtime_conversation_operation(selected, request.idempotency_key)


@_conversation_rpc("runtime.conversation.command.receipt", "RuntimeConversationCommandReceiptParams")
def _conversation_command_receipt(rid, request, context, config, db):
    from agent.conversation_identity import recorded_conversation_scope

    selected, _ = recorded_conversation_scope(context, config, db, request.conversation_id)
    owned = db.read_runtime_conversation(selected, request.conversation_id)
    # Validate the complete persisted binding/lineage before reading its journal.
    # Historical inspection never binds a live session or grants execution authority.
    command = db.read_runtime_command_receipt(owned["lineage"][-1], request.command_id,
        message_cursor=request.message_cursor, message_limit=request.message_limit)
    return _runtime_command_receipt_projection(request.command_id, command)


@_conversation_rpc("runtime.memory.list", "RuntimeConversationParams")
def _memory_list(rid, request, context, config, db):
    from hermes_cli.memory_conversations import memory_service, list_memory_conversations
    return list_memory_conversations(memory_service(context, config, db))


@_conversation_rpc("runtime.memory.open", "RuntimeMemoryOpenParams")
def _memory_open(rid, request, context, config, db):
    from contextlib import contextmanager
    from hermes_cli.memory_conversations import memory_service, open_memory_conversation
    from hermes_cli.memory_sessions import ConversationError

    @contextmanager
    def idle_scope(root):
        popped = None
        try:
            with _session_resume_lock:
                tip = db.resolve_resume_session_id(root)
                live = _find_live_session_by_key(tip, _hermes_home)
                if live:
                    sid, session = live
                    with _sessions_lock:
                        if (session.get("transport") is not _stdio_transport or session.get("running")
                                or _session_pending_kind(sid) or _session_has_active_delegations(sid, session)):
                            raise ConversationError("Wait for the active conversation and its tasks to finish before syncing history.")
                def invalidate():
                    nonlocal popped
                    if live:
                        with _sessions_lock:
                            if session.get("running") or _session_pending_kind(sid):
                                raise ConversationError("Wait for the current response before syncing history.")
                            popped = _pop_session_by_id(sid)
                yield invalidate
        finally:
            if popped is not None:
                _teardown_popped_session(popped, end_reason="memory_resume")

    return open_memory_conversation(memory_service(context, config, db), request.source, request.session_id,
                                    idle_scope=idle_scope)


def register(server):
    bind_module(globals(), server)
