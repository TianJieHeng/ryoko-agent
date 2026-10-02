"""An empty session has one authority binding, even across independent DB handles."""
import pytest
from hermes_state import SessionDB


def test_binding_is_single_assignment_and_preserves_other_metadata(tmp_path):
    first = SessionDB(tmp_path / "state.db")
    second = SessionDB(tmp_path / "state.db")
    first.create_session("fixture", source="cli", model_config={"max_iterations": 4})
    binding = {"agent_id": "fixture_a", "binding_revision": 1}
    first.claim_session_agent_identity("fixture", binding)
    second.claim_session_agent_identity("fixture", dict(binding))
    with pytest.raises(ValueError, match="conflict"):
        second.claim_session_agent_identity("fixture", {**binding, "agent_id": "fixture_b"})
    assert first.get_session_model_config_value("fixture", "agent_identity") == binding
    assert first.get_session_model_config_value("fixture", "max_iterations") == 4
    first.close(); second.close()


def test_unbound_legacy_transcript_cannot_be_adopted(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("legacy", source="cli")
    db.append_message("legacy", "user", "private historical record")
    with pytest.raises(ValueError, match="migration"):
        db.claim_session_agent_identity("legacy", {"agent_id": "fixture_a"})
    assert db.get_session_model_config_value("legacy", "agent_identity") is None
    db.close()
