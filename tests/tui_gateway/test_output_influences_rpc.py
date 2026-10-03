"""Unavailable personal harness never gains invented record mutation semantics."""
from tests.tui_gateway.test_memory_rpc import memory, result, denied  # noqa: F401


def test_primary_unavailable_harness_is_explicit_and_no_hidden_store(memory):
    response = result(memory.call("runtime.memory.output.list", "primary"))
    assert response["outputs"] == [] and response["backend"] == "personal_mcp"
    assert response["unavailable_reason"] == "no_verified_output_context"
    denied(memory.call("runtime.memory.output.control", "primary", control_id="fake", run_id="unknown",
        context_sha256="0" * 64, record_id="remote", expected_version=1, namespace_id="invented",
        action="remove", scope="general"), "memory_operation_unsupported")
    assert not (memory.homes["home"] / "individual-memory").exists()
