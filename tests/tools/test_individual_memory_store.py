"""Real identity/private-catalog/projection operations, including process crashes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.agent_identity import IdentityPolicyError, resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.secret_scope import reset_multiplex_context, set_multiplex_context
from tools.individual_memory_store import IndividualMemoryError, create_individual_memory_store
from tools.memory_tool import MemoryStore, get_memory_dir, load_on_disk_store, memory_tool

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def identities(tmp_path):
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "a", "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"},
            "a": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin", "allowed_tools": ["memory"]},
            "b": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin", "allowed_tools": ["memory"]}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin", "allowed_tools": ["memory"]}}}
    homes = [tmp_path / "home-a", tmp_path / "home-b"]
    for home in homes:
        home.mkdir(mode=0o700)
        (home / "config.yaml").write_text(json.dumps(raw))
    def context(actor="a", home=homes[0], *, child=False, stored=None):
        config = json.loads(json.dumps(raw))
        config["agent_identity"]["active_agent_id"] = actor
        # active_agent_id is construction selection in trusted configuration;
        # it participates in config digest, so install exact config for each call.
        (home / "config.yaml").write_text(json.dumps(config))
        parent = resolve_agent_context(config, session_id="parent", profile_home=home)
        return resolve_agent_context(config, session_id="child" if child else "session", profile_home=home,
             parent_context=parent if child else None, is_child=child, stored_binding=stored)
    @contextmanager
    def scope(actor="a", home=homes[0], **kwargs):
        ctx = context(actor, home, **kwargs)
        with agent_runtime_scope(ctx):
            yield ctx
    token = set_multiplex_context(True)
    try:
        yield scope, context, homes, raw
    finally:
        reset_multiplex_context(token)


def test_real_scope_alternation_direct_utility_and_primary_denial(identities):
    scope, _, homes, _ = identities
    with scope() as a:
        store = create_individual_memory_store(a)
        store.load_from_disk()
        assert store.add("memory", "A-only fact")["success"]
        assert get_memory_dir() == store._scope.directory
        assert load_on_disk_store().memory_entries == ["A-only fact"]
        a_path = store._path_for("memory")
        with pytest.raises(IdentityPolicyError):
            MemoryStore()
    with scope("b") as b:
        other = create_individual_memory_store(b)
        other.load_from_disk()
        assert other.memory_entries == []
        for access in (lambda: store.recall(), lambda: store.memory_entries,
                       lambda: store.format_for_system_prompt("memory"), lambda: store.add("memory", "leak"),
                       lambda: memory_tool(action="add", content="leak", store=store),
                       lambda: MemoryStore._read_file(a_path)):
            with pytest.raises(IdentityPolicyError):
                access()
    with scope("a", homes[1]) as a_other_home:
        assert create_individual_memory_store(a_other_home).recall() == []
        with pytest.raises(IdentityPolicyError):
            store.recall()
    with scope():
        assert store.recall()[0]["content"] == "A-only fact"
    with scope("primary") as primary:
        for construct in (lambda: create_individual_memory_store(primary), load_on_disk_store, MemoryStore):
            with pytest.raises(IdentityPolicyError):
                construct()
    assert all(not (home / "memories").exists() for home in homes)


def test_structured_cas_supersession_deletion_and_frozen_snapshot(identities):
    scope, _, _, _ = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        added = store.write_record("Prefers short answers", kind="preference", confidence=1)
        record = added["record"]
        store.load_from_disk()
        prefix = store.format_for_system_prompt("memory")
        revised = store.write_record("Prefers detailed answers", record_id=record["record_id"], expected_version=1, kind="preference")
        assert revised["acknowledged_version"] == 2
        assert store.read_record(record["record_id"], version=1)["validity"] == "superseded"
        conflict = store.write_record("Conflicting correction", record_id=record["record_id"], expected_version=1)
        assert not conflict["success"] and conflict["current_version"] == 2 and conflict["conflict_id"]
        store.load_from_disk()
        assert store.format_for_system_prompt("memory") == prefix
        changes = store.changes_since(1)
        assert changes["records"][0]["version"] == 2
        assert store.delete_record(record["record_id"], expected_version=2)["success"]
        assert store.recall() == [] and store.export_records() == []
        assert store.read_record(record["record_id"], version=1)["content"] is None
        assert store.changes_since(2)["records"][0]["deletion_state"] == "deleted"
        assert "Prefers" not in store._path_for("memory").read_text()
        snapshot = store.export_snapshot(include_deleted=True)
        assert snapshot["revision"] == 3 and snapshot["records"][0]["content"] is None
        assert store.format_for_system_prompt("memory") == prefix
        store.refresh_snapshot()
        assert store.format_for_system_prompt("memory") is None


def test_concurrent_cas_one_winner_and_atomic_legacy_batch(identities):
    scope, _, _, _ = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        record = store.write_record("baseline")["record"]
        def correct(index):
            with agent_runtime_scope(ctx):
                return create_individual_memory_store(ctx).write_record(
                    f"candidate {index}", record_id=record["record_id"], expected_version=1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(correct, range(4)))
        assert sum(result["success"] for result in results) == 1
        assert sum(result.get("code") == "version_conflict" for result in results) == 3
        store.load_from_disk()
        before = store.export_snapshot()
        failed = store.apply_batch("memory", [{"action": "add", "content": "extra"},
                                             {"action": "remove", "old_text": "absent"}])
        assert not failed["success"] and store.export_snapshot() == before
        winner = store.recall()[0]["content"]
        assert store.replace("memory", winner, "next")["success"]
        assert store.recall()[0]["version"] == 3


@pytest.mark.parametrize("crash_point", ["committed", "first_projection"])
def test_actual_process_death_recovers_catalog_projection(identities, crash_point):
    scope, _, homes, _ = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        store.load_from_disk()
        script = r'''
import json, os, sys
from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from tools.individual_memory_store import create_individual_memory_store
home, point = sys.argv[1:]
raw = json.load(open(home + '/config.yaml'))
context = resolve_agent_context(raw, session_id='crash', profile_home=home)
with agent_runtime_scope(context):
    store = create_individual_memory_store(context)
    original = store._write_projection
    calls = []
    def crash(directory, name, data):
        calls.append(name)
        if point == 'first_projection':
            original(directory, name, data)
        os._exit(77)
    store._write_projection = crash
    store.write_record('durable after crash')
'''
        result = subprocess.run([sys.executable, "-c", script, str(homes[0]), crash_point], check=False)
        assert result.returncode == 77
        recovered = create_individual_memory_store(ctx)
        recovered.load_from_disk()
        assert recovered.recall()[0]["content"] == "durable after crash"
        assert "durable after crash" in recovered._path_for("memory").read_text()
        assert recovered.current_revision() == 1
        assert len(recovered.export_records()) == 1


def test_external_drift_symlink_and_limits_fail_closed(identities):
    scope, _, homes, _ = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx, memory_char_limit=10)
        store.write_record("short")
        with pytest.raises(IndividualMemoryError, match="budget"):
            store.write_record("too long content")
        path = store._path_for("memory")
        path.write_text("outside edit retained")
        with pytest.raises(IndividualMemoryError, match="outside the catalog"):
            store.add("memory", "x")
        assert path.read_text() == "outside edit retained"
        path.unlink()
        assert store.recall()[0]["content"] == "short"
        path.unlink()
        secret = homes[0] / "private.txt"
        secret.write_text("never read")
        path.symlink_to(secret)
        with pytest.raises(OSError):
            store.recall()
        assert secret.read_text() == "never read"


def test_child_resume_retention_and_unrelated_child_namespace(identities):
    scope, _, _, _ = identities
    with scope(child=True) as child:
        store = create_individual_memory_store(child)
        store.write_record("child work reference")
        binding = child.identity.to_record()
        namespace = store.namespace_id
        store.close()
    with scope(child=True) as another:
        assert create_individual_memory_store(another).namespace_id != namespace
        assert create_individual_memory_store(another).recall() == []
    with scope(child=True, stored=binding) as resumed:
        retained = create_individual_memory_store(resumed)
        assert retained.namespace_id == namespace and retained.recall()[0]["content"] == "child work reference"
        assert retained.retention("retained")["automatic_deletion"] is False
        retained.retention("archived")
        assert retained.recall()
        with pytest.raises(IndividualMemoryError, match="Archived"):
            retained.add("memory", "not written")
        retained.retention("active")
        assert retained.add("memory", "resumed")["success"]


def test_project_corrections_apply_only_to_explicit_granted_context(identities, monkeypatch, tmp_path):
    from hermes_cli import projects_db as pdb
    from tools.capability_broker import CapabilityDenied
    scope, _, homes, raw = identities
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with scope():
        with pdb.connect_closing() as db:
            project = pdb.create_project(db, name="One", owner_principal_id="owner", grants=[
                {"principal_id": "owner", "agent_id": "a", "permissions": ["read", "write"]}])
            other = pdb.create_project(db, name="Two", owner_principal_id="owner", grants=[
                {"principal_id": "owner", "agent_id": "a", "permissions": ["read", "write"]}])
    raw["agent_identity"]["agents"]["a"]["project_grants"] = [project, other]
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        store.write_record("General convention")
        private = store.write_record("Use the blue diagram", kind="preference", scope=f"project:{project}")["record"]
        store.load_from_disk()
        assert "blue" not in store.format_for_system_prompt("memory")
        assert len(store.recall()) == 1 and len(store.changes_since(0)["records"]) == 1
        assert len(store.recall(project_id=other)) == 1
        assert len(store.changes_since(0, project_id=other)["records"]) == 1
        assert len(store.recall(project_id=project)) == 2
        assert store.read_record(private["record_id"])["scope"] == f"project:{project}"
        assert len(store.export_snapshot(project_id=project)["records"]) == 2
        with pdb.connect_closing() as db:
            db.execute("DELETE FROM project_grants WHERE project_id=?", (project,))
            db.commit()
        for action in (lambda: store.recall(project_id=project),
                       lambda: store.read_record(private["record_id"]),
                       lambda: store.write_record("move to global", record_id=private["record_id"], expected_version=1),
                       lambda: store.delete_record(private["record_id"], expected_version=1),
                       lambda: store.export_snapshot(project_id=project)):
            with pytest.raises(CapabilityDenied):
                action()
        assert len(store.recall()) == 1
    assert not (homes[0] / "memories" / "MEMORY.md").exists()
    assert not (homes[0] / "memories" / "USER.md").exists()


def test_strict_queue_never_receives_payload_and_policy_revocation_denies(identities, monkeypatch):
    from tools import write_approval as wa
    from types import SimpleNamespace
    from tools.capability_broker import CapabilityDenied
    scope, _, homes, raw = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        store.load_from_disk()
        monkeypatch.setattr(wa, "evaluate_gate", lambda *_a, **_kw: SimpleNamespace(allow=False))
        monkeypatch.setattr(wa, "stage_write", lambda *_a, **_kw: pytest.fail("Private payload reached shared queue"))
        response = json.loads(memory_tool(action="add", content="private fact", store=store))
        assert not response["success"] and "unsupported" in response["error"]
        assert store.recall() == []
        raw["agent_identity"]["agents"]["a"]["allowed_tools"] = []
        (homes[0] / "config.yaml").write_text(json.dumps(raw))
        with pytest.raises(CapabilityDenied):
            store.recall()


def test_structured_record_validation_and_bounded_delta(identities):
    scope, _, _, _ = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        for metadata in ({"confidence": float("nan")}, {"confidence": 1.1}, {"validity": "certain"},
                         {"kind": "procedure_reference"}, {"kind": "procedure_reference", "source_ref": "workflow:unversioned"},
                         {"valid_from": 2, "valid_to": 1},
                         {"scope": "everyone"}):
            with pytest.raises(IndividualMemoryError):
                store.write_record("test", **metadata)
        row = store.write_record("real procedure", kind="procedure_reference", source_ref="workflow:procedure-1:2")["record"]
        assert row["validity"] == "uncertain"
        conflict = store.write_record("proposed", record_id=row["record_id"], expected_version=0)
        assert store.read_conflict(conflict["conflict_id"])["proposed_record"]["content"] == "proposed"
        delta = store.changes_since(0, max_chars=128)
        assert delta["truncated"] and delta["revision"] == 0
        assert store.changes_since(0)["revision"] == 1
        disabled = create_individual_memory_store(ctx, memory_enabled=False)
        with pytest.raises(IndividualMemoryError, match="disabled"):
            disabled.write_record("disabled")


def test_time_window_invalidation_does_not_rewrite_prefix(identities, monkeypatch):
    import tools.individual_memory_store as memory
    scope, _, _, _ = identities
    with scope() as ctx:
        store = create_individual_memory_store(ctx)
        now = memory.time.time()
        row = store.write_record("Temporary convention", valid_from=now - 10, valid_to=now + 10)["record"]
        store.load_from_disk()
        prefix = store.format_for_system_prompt("memory")
        assert store.recall()[0]["record_id"] == row["record_id"]
        monkeypatch.setattr(memory.time, "time", lambda: now + 20)
        assert store.recall() == []
        delta = store.changes_since(row["revision"])
        assert delta["revision"] == row["revision"]
        assert delta["records"][0]["content"] is None and delta["records"][0]["validity"] == "invalid"
        assert store.format_for_system_prompt("memory") == prefix


@pytest.mark.parametrize("disabled_target", ["memory", "user"])
def test_persisted_disabled_target_has_zero_read_or_fresh_payload(identities, disabled_target):
    from agent.memory_router import RoutedMemoryManager
    scope, _, _, raw = identities
    with scope() as ctx:
        writer = create_individual_memory_store(ctx)
        records, conflicts = {}, {}
        for target in ("memory", "user"):
            record = writer.write_record(f"retained {target} content", target=target)["record"]
            records[target] = record
            conflicts[target] = writer.write_record(f"pending {target} correction", target=target,
                record_id=record["record_id"], expected_version=0)["conflict_id"]
            deleted = writer.write_record(f"deleted {target} content", target=target)["record"]
            writer.delete_record(deleted["record_id"], expected_version=1)
        revision = writer.current_revision()
        limited = create_individual_memory_store(ctx, memory_enabled=disabled_target != "memory",
                                                  user_profile_enabled=disabled_target != "user")
        limited.load_from_disk()
        enabled_target = "user" if disabled_target == "memory" else "memory"
        assert getattr(limited, f"{disabled_target}_entries") == []
        assert limited.format_for_system_prompt(disabled_target) is None
        assert getattr(limited, f"{enabled_target}_entries") == [f"retained {enabled_target} content"]
        for result in (limited.recall(), limited.export_records(),
                       limited.export_snapshot(include_deleted=True)["records"],
                       limited.changes_since(0)["records"], limited.changes_since(revision)["records"]):
            assert all(row["target"] == enabled_target for row in result)
        manager = RoutedMemoryManager(ctx, raw, store=limited)
        packet = manager.fresh_context("recall prior context", session_id="session")
        assert [row["record_id"] for row in packet["records"] if row["content"]] == [records[enabled_target]["record_id"]]
        assert f"retained {disabled_target} content" not in json.dumps(packet)
        assert f"pending {disabled_target} correction" not in json.dumps(packet)
        for action in (lambda: limited.read_record(records[disabled_target]["record_id"]),
                       lambda: limited.read_record(records[disabled_target]["record_id"], version=1),
                       lambda: limited.read_conflict(conflicts[disabled_target]),
                       lambda: limited.remove(disabled_target, f"retained {disabled_target} content")):
            with pytest.raises(IndividualMemoryError) as denied:
                action()
            assert denied.value.code == "memory_disabled"
        reopened = create_individual_memory_store(ctx)
        assert reopened.current_revision() == revision
        assert {row["target"] for row in reopened.recall()} == {"memory", "user"}
        assert reopened.read_record(records[disabled_target]["record_id"])["content"] == f"retained {disabled_target} content"
