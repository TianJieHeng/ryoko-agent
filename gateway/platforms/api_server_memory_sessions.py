"""Memory-provider continuity across api_server requests (#120116).

The api_server adapter builds a fresh ``AIAgent`` per request (per-request callbacks, model
route, ephemeral prompt), unlike the messaging platforms, whose cached agent — and with it the
memory provider — lives for the whole session. External providers deliver recall as the
PREVIOUS turn's background prefetch held on the provider instance, so a provider that is
re-initialised per request never has anything to inject, and each init re-runs the provider's
startup (for an embedded daemon: a restart that also kills the retain still in flight).

This registry keeps one initialised ``MemoryManager`` per (profile home, session id): a request
checks the session's manager out before building its agent (``AIAgent(memory_manager=...)``
skips provider init) and checks it back in when the turn ends. Check-out is exclusive, so two
concurrent requests on one session never share a manager; the loser's fresh manager is shut down
when it checks in behind the winner. Idle entries and LRU overflow are shut down under the owning
profile's scope, like the gateway agent cache's eviction.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ApiServerMemorySessions:
    """Session-keyed ``MemoryManager`` registry with exclusive check-out/check-in."""

    def __init__(self, *, max_size: Optional[int] = None, idle_ttl_secs: Optional[float] = None) -> None:
        self._entries: "OrderedDict[Tuple[str, ...], Tuple[Any, Optional[Path], float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._idle_ttl_secs = idle_ttl_secs

    # -- bounds (same knobs as the gateway agent cache, resolved lazily) -------------------------

    def _bounds(self) -> Tuple[int, float]:
        if self._max_size is None or self._idle_ttl_secs is None:
            from gateway.run import _AGENT_CACHE_IDLE_TTL_SECS, _AGENT_CACHE_MAX_SIZE, _load_gateway_config
            from gateway.agent_cache_pressure import resolve_agent_cache_bounds
            configured = None
            with suppress(Exception):
                configured = resolve_agent_cache_bounds(_load_gateway_config())
            if self._max_size is None:
                self._max_size = getattr(configured, "max_size", None) or _AGENT_CACHE_MAX_SIZE
            if self._idle_ttl_secs is None:
                self._idle_ttl_secs = getattr(configured, "idle_ttl_secs", None) or _AGENT_CACHE_IDLE_TTL_SECS
        return self._max_size, self._idle_ttl_secs

    @staticmethod
    def _owner_home() -> Tuple[str, Optional[Path]]:
        """(registry key, profile home to re-enter on eviction) for the CURRENT scope. Callers run
        inside ``_profile_scope`` (or a single-profile gateway), so the ambient home is the owner's."""
        from hermes_constants import get_hermes_home, hermes_home_key
        home = Path(get_hermes_home())
        return hermes_home_key(home), home

    # -- check-out / check-in ----------------------------------------------------------------

    def checkout(self, session_id: Optional[str], *, session_db=None) -> Optional[Any]:
        """Exclusive checkout by immutable identity, backend and credential scope."""
        if not session_id:
            return None
        home_key, _home = self._owner_home()
        from agent.runtime_context import current_agent_context
        from agent.identity_lifecycle import identity_config, agent_runtime_scope, _stored_binding
        from agent.agent_identity import parse_agent_identity_config, resolve_agent_context
        config = identity_config()
        context = current_agent_context()
        strict = parse_agent_identity_config(config) is not None
        if strict and context is None:
            # API requests reach this before AIAgent construction; only an existing
            # durable session binding can authorize reuse of its private manager.
            if session_db is None or _home is None:
                return None
            stored = _stored_binding(session_db, session_id)
            if not stored:
                return None
            identity_session_id = session_id
            if stored.get("session_id") != session_id:
                original = stored.get("session_id")
                if (isinstance(original, str) and session_db.get_session(original) is not None
                        and session_db._session_turn_lease_key(session_id) == session_db._session_turn_lease_key(original)):
                    identity_session_id = original
            context = resolve_agent_context(config, session_id=identity_session_id,
                profile_home=str(_home), stored_binding=stored)
        key = (home_key, session_id)
        if context is not None:
            from agent.memory_router import memory_scope_digest
            with agent_runtime_scope(context):
                key += (memory_scope_digest(context, config),)
        with self._lock:
            entry = self._entries.pop(key, None)
        if entry is None:
            return None
        manager = entry[0]
        if context is not None:
            from agent.memory_router import RoutedMemoryManager
            with agent_runtime_scope(context):
                if not isinstance(manager, RoutedMemoryManager) or not manager.matches(context, config):
                    raise PermissionError("Cached memory owner or configuration changed")
                manager.assert_owner()
        return manager

    def checkin(self, agent: Any) -> None:
        """Park ``agent``'s manager under the session the turn ended on (``agent.session_id`` carries a
        mid-turn compression rotation) and shut down whatever this displaces or has gone idle."""
        manager = getattr(agent, "_memory_manager", None)
        session_id = str(getattr(agent, "session_id", "") or "")
        if manager is None or not session_id:
            return
        home_key, home = self._owner_home()
        key = (home_key, session_id)
        from agent.runtime_context import current_agent_context
        from agent.identity_lifecycle import agent_runtime_scope, strict_identity_enabled
        from agent.memory_router import RoutedMemoryManager
        context = getattr(agent, "runtime_context", None)
        if context is not None:
            current = current_agent_context()
            if current is not None and current != context:
                raise PermissionError("Cannot park another agent's memory manager")
            if not isinstance(manager, RoutedMemoryManager) or manager.owner_context != context:
                raise PermissionError("Memory manager does not belong to the agent")
            with agent_runtime_scope(context):
                manager.assert_owner()
                home_key, home = self._owner_home()
                key = (home_key, session_id, manager.cache_scope_digest)
        elif isinstance(manager, RoutedMemoryManager) or strict_identity_enabled():
            raise PermissionError("Strict memory pooling requires the authenticated agent")
        max_size, idle_ttl = self._bounds()
        now = time.monotonic()
        doomed: List[Tuple[Any, Optional[Path]]] = []
        with self._lock:
            displaced = self._entries.pop(key, None)
            if displaced is not None and displaced[0] is not manager:
                doomed.append((displaced[0], displaced[1]))
            self._entries[key] = (manager, home, now)
            for key, (mgr, owner, last_used) in list(self._entries.items()):
                if mgr is manager:
                    continue
                if now - last_used > idle_ttl or len(self._entries) > max_size:
                    del self._entries[key]
                    doomed.append((mgr, owner))
        for mgr, owner in doomed:
            self._shutdown_async(mgr, owner)

    def close_all(self) -> None:
        """Adapter shutdown: drain and shut down every parked manager (inline: the process is ending)."""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for mgr, owner, _ in entries:
            self._shutdown(mgr, owner)

    # -- teardown -------------------------------------------------------------------------------

    def _shutdown_async(self, manager: Any, owner: Optional[Path]) -> None:
        """Eviction runs inside a request's own turn: never make that reply wait on a provider drain."""
        from agent.memory_provider import spawn_context_thread
        spawn_context_thread(self._shutdown, args=(manager, owner), name="api-server-memory-evict").start()

    @staticmethod
    def _shutdown(manager: Any, owner: Optional[Path]) -> None:
        """Bounded drain then provider shutdown, under the OWNING profile's scope: eviction runs inside
        whichever request happened to trigger it, and a provider reads its home/credentials at call time."""
        from agent.memory_router import RoutedMemoryManager
        if isinstance(manager, RoutedMemoryManager):
            from agent.identity_lifecycle import agent_runtime_scope
            if owner is None or Path(manager.owner_context.profile_home).resolve() != owner.resolve():
                raise PermissionError("Memory teardown owner does not match its bound home")
            # Never carry the evicting request's identity into the provider lifecycle.
            try:
                with agent_runtime_scope(manager.owner_context):
                    manager.shutdown_all()
            except (PermissionError, ValueError):
                # Revocation forbids provider payloads; dropping the parked reference
                # needs no flush and must not prevent other owned managers closing.
                logger.debug("Revoked memory manager discarded without provider lifecycle payloads")
            return
        scope: Any = nullcontext()
        with suppress(Exception):
            from agent.secret_scope import is_multiplex_active
            if owner is not None and is_multiplex_active():
                from gateway.run import _profile_runtime_scope
                scope = _profile_runtime_scope(owner)
        try:
            with scope:
                with suppress(Exception):
                    manager.flush_pending(timeout=10)
                manager.shutdown_all()
        except Exception:
            logger.debug("api_server memory manager shutdown failed", exc_info=True)

    # -- introspection (tests) --------------------------------------------------------------------

    def parked(self) -> Dict[Tuple[str, ...], Any]:
        with self._lock:
            return {key: entry[0] for key, entry in self._entries.items()}
