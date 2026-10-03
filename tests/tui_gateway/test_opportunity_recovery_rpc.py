"""Schema45 candidate choices survive real owning-store bundles and reject foreign ownership."""
import io
import json
import sqlite3
import zipfile

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result  # noqa: F401
from tests.tui_gateway.test_opportunities_rpc import choose, discover
from tests.tui_gateway.test_workflows_rpc import create_workflow

pytestmark = pytest.mark.platforms("linux")


def test_owned_review_bundle_restore_preserves_history_and_rejects_cross_owner(artifacts, tmp_path):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.operations_control import OperationsError
    from hermes_cli.operations_profile_recovery import snapshot_owning_stores, verify_owning_store_restore
    from hermes_state_common import SCHEMA_VERSION
    project = artifacts.project()["id"]
    create_workflow(artifacts, project)
    candidate = discover(artifacts, project)["candidates"][0]
    dismissed = result(choose(artifacts, candidate, "dismissed"))["candidate"]
    agent = artifacts.agents["a"]
    with agent_runtime_scope(agent.runtime_context):
        archive = snapshot_owning_stores(agent._session_db, agent.runtime_context, staging_directory=tmp_path, code_version="opportunity-fixture")
        verified = verify_owning_store_restore(archive, agent.runtime_context, expected_store_schema=SCHEMA_VERSION,
            expected_code_version="opportunity-fixture", temporary_parent=tmp_path)
    assert verified["all_supported_owning_stores_validated"] and not verified["live_profile_modified"]
    assert verified["external_effects_replayed"] == 0
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        recovered = tmp_path / "review-recovered.db"
        recovered.write_bytes(bundle.read("state.db"))
    with sqlite3.connect(recovered) as conn:
        row = conn.execute("SELECT record_json FROM opportunity_candidates WHERE candidate_id=?", (candidate["candidate_id"],)).fetchone()
        assert json.loads(row[0])["disposition"] == dismissed["disposition"]
        assert conn.execute("SELECT COUNT(*) FROM opportunity_history").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM opportunity_requests").fetchone()[0] == 2
    # Every review table is a supported owning-store surface, not an unchecked payload island.
    for table in ("opportunity_candidates", "opportunity_history", "opportunity_requests"):
        with agent._session_db._runtime_read() as conn:
            original = conn.execute(f"SELECT owner_json FROM {table} LIMIT 1").fetchone()[0]
        agent._session_db._write_sql(f"UPDATE {table} SET owner_json=?", (json.dumps({"principal_id": "foreign"}),))
        with agent_runtime_scope(agent.runtime_context):
            with pytest.raises(OperationsError, match="opportunity scope mismatch"):
                snapshot_owning_stores(agent._session_db, agent.runtime_context, staging_directory=tmp_path, code_version="opportunity-fixture")
        agent._session_db._write_sql(f"UPDATE {table} SET owner_json=?", (original,))
    assert discover(artifacts, project, "after-drill")["candidates"] == []
