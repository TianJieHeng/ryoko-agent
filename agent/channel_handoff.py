"""Verified owned-surface mappings feed the one durable command journal.

Bindings are a projection of an already authenticated session, never login
credentials. A binding ID alone grants no access and cannot reattach a foreign
transport. External channel identity adapters are deliberately unsupported.
"""
from __future__ import annotations

import json
import time

from agent.bounded_services import canonical, digest, require
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor

LOCAL_CHANNELS = frozenset({"local_jsonrpc", "voice", "screen"})


class ChannelHandoff:
    def __init__(self, agent):
        from gateway.durable_outbox import _authority
        self.context, self.db, self.sid, self.actor = _authority(agent)
        self.access = project_access(self.context)

    def _mission(self):
        mission = self.db.get_mission(self.sid, self.actor, access=self.access)
        require(mission is not None, "handoff_mission_required")
        require(isinstance(mission.get("project_id"), str) and mission["project_id"], "handoff_project_required")
        return mission

    def bind(self, channel):
        require(channel in LOCAL_CHANNELS, "channel_adapter_unconfigured")
        mission = self._mission()
        binding_id = "binding_" + digest(canonical({"owner": self.actor, "mission_id": mission["mission_id"],
            "channel": channel}).encode())
        with self.access.guard(mission["project_id"], self.actor, "read"):
            def write(conn):
                root = self.db._mission_owner_on_conn(conn, self.sid, self.actor)
                old = conn.execute("SELECT * FROM runtime_channel_bindings WHERE binding_id=?", (binding_id,)).fetchone()
                if old is None:
                    require(conn.execute("SELECT COUNT(*) FROM runtime_channel_bindings").fetchone()[0] < 4096,
                            "handoff_capacity")
                    conn.execute("INSERT INTO runtime_channel_bindings VALUES(?,?,?,?,?,?,?)",
                        (binding_id, canonical(self.actor), root, mission["project_id"], mission["mission_id"], channel, time.time()))
                return {"binding_id": binding_id, "mission_id": mission["mission_id"],
                        "project_id": mission["project_id"], "channel": channel,
                        "verification": "owned_live_transport_and_stored_identity", "history_replayed": False}
            return self.db._execute_write(write)

    def envelope(self, binding_id, input_id, operation, payload, expected_revision=None):
        require(isinstance(input_id, str) and 0 < len(input_id) <= 256, "invalid_command")
        mission = self._mission()
        with self.access.guard(mission["project_id"], self.actor, "read"), self.db._runtime_read() as conn:
            row = conn.execute("SELECT * FROM runtime_channel_bindings WHERE binding_id=?", (binding_id,)).fetchone()
            require(row is not None and json.loads(row["owner_json"]) == self.actor, "handoff_not_owned")
            root = self.db._mission_owner_on_conn(conn, self.sid, self.actor)
            require(row["session_id"] == root and row["mission_id"] == mission["mission_id"]
                    and row["project_id"] == mission["project_id"], "handoff_mission_changed")
            require(row["channel"] in LOCAL_CHANNELS, "channel_adapter_unconfigured")
        # Channel-independent identity intentionally deduplicates one logical
        # input across voice/desktop reconnect. A changed payload conflicts in
        # the existing journal instead of being interpreted as another task.
        command_id = "input_" + digest(canonical({"actor": self.actor, "mission_id": mission["mission_id"],
            "input_id": input_id}).encode())
        return {"schema_version": 1, "command_id": command_id, "idempotency_key": command_id,
                "expected_revision": expected_revision, "operation": operation, "payload": payload}
