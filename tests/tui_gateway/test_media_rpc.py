"""BE13 real owned service/artifact/command paths with no live media or external data."""
import base64
import hashlib
import json

import httpx
import openai
import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, publish  # noqa: F401
from tests.agent.test_media_ingress import png


def media_result(response):
    return json.loads(result(response)["response_json"])


def source(rpc, text="# Agenda\r\nCall Ada at 12\r\n"):
    project = rpc.project()["id"]
    row = publish(rpc, {"project_id": project, "command_id": "source", "request_id": "source", "content": text})
    return project, row


def prepare(rpc, project, row, request_id="pipeline"):
    return media_result(rpc.call("runtime.services.prepare", project_id=project, artifact_id=row["artifact_id"],
                                version=row["version"], request_id=request_id))


def test_two_stage_transfer_receipts_resume_after_service_loss_without_replay(artifacts, monkeypatch):
    from agent.bounded_services import BoundedServices, DOCUMENT, NORMALIZE, _ADAPTERS
    from tui_gateway import server
    from hermes_state import SessionDB
    project, row = source(artifacts)
    prepared = prepare(artifacts, project, row)
    manifest, checksum = prepared["manifest"], prepared["manifest_sha256"]
    assert manifest["transfer"]["input_bytes"] == row["size"] and manifest["transfer"]["remote_bytes"] == 0
    assert manifest["executor"]["location"] == "local"
    assert prepare(artifacts, project, row) == prepared
    pid = manifest["pipeline_id"]
    status = media_result(artifacts.call("runtime.services.status", pipeline_id=pid))
    assert status["state"] == "pending" and not status["receipts"]
    denied(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256="0" * 64), "service_manifest_mismatch")
    monkeypatch.setattr(server, "_media_services", lambda agent, db: BoundedServices(agent.runtime_context, db, unavailable={DOCUMENT.service_id}))
    partial = media_result(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256=checksum))
    assert partial["state"] == "partial" and partial["next_stage"] == 1
    assert partial["blocked_reason"] == "service_unavailable"
    denied(artifacts.call("runtime.services.output", pipeline_id=pid), "service_output_pending")
    # Durable stage survives reopening the actual SQLite store. Reexecuting its
    # adapter would fail, proving downstream recovery consumes committed bytes.
    agent = artifacts.agents["a"]
    path = agent._session_db.db_path
    agent._session_db.close()
    agent._session_db = SessionDB(path)
    def forbidden(_data):
        raise AssertionError("Completed stage must not be replayed")
    monkeypatch.setitem(_ADAPTERS, NORMALIZE.service_id, forbidden)
    monkeypatch.setattr(server, "_media_services", lambda agent, db: BoundedServices(agent.runtime_context, db))
    completed = media_result(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256=checksum))
    assert completed["state"] == "completed" and completed["receipts"][0] == partial["receipts"][0]
    assert completed["receipts"][0]["output_sha256"] == completed["receipts"][1]["input_sha256"]
    output = media_result(artifacts.call("runtime.services.output", pipeline_id=pid))
    data = base64.b64decode(output["content_base64"], validate=True)
    assert hashlib.sha256(data).hexdigest() == output["receipt"]["output_sha256"]
    assert json.loads(data)["headings"][0]["text"] == "Agenda"
    assert json.loads(data)["text"] == "# Agenda\nCall Ada at 12\n"
    assert media_result(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256=checksum)) == completed
    with agent._session_db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM bounded_service_stages WHERE pipeline_id=?", (pid,)).fetchone()[0] == 2
        # The pipeline did not publish a second canonical project artifact.
        assert conn.execute("SELECT COUNT(*) FROM runtime_artifact_versions").fetchone()[0] == 1
    agent._session_db._execute_write(lambda conn: conn.execute(
        "UPDATE bounded_service_stages SET output_bytes=? WHERE pipeline_id=? AND stage=1", (b"changed", pid)))
    denied(artifacts.call("runtime.services.output", pipeline_id=pid), "service_digest_mismatch")


def test_live_identity_project_revocation_and_executor_reprepare_are_exact(artifacts):
    from agent.executor_capabilities import disconnect_executor
    from agent.identity_lifecycle import agent_runtime_scope
    project, row = source(artifacts)
    prepared = prepare(artifacts, project, row)
    pid = prepared["manifest"]["pipeline_id"]
    for label in ("a", "b", "a"):
        assert len(media_result(artifacts.call("runtime.services.capabilities", label))["services"]) == 2
    denied(artifacts.call("runtime.services.status", "b", pipeline_id=pid), "service_pipeline_not_found")
    denied(artifacts.call("runtime.services.status", via=artifacts.peers["b"], pipeline_id=pid))
    with agent_runtime_scope(artifacts.agents["a"].runtime_context):
        disconnect_executor(artifacts.agents["a"].runtime_context, prepared["manifest"]["executor"])
    blocked = media_result(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256=prepared["manifest_sha256"]))
    assert blocked["state"] == "pending" and blocked["blocked_reason"] == "executor_disconnected"
    refreshed = prepare(artifacts, project, row)
    assert refreshed["manifest_sha256"] != prepared["manifest_sha256"]
    denied(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256=prepared["manifest_sha256"]), "service_manifest_mismatch")
    project_row = result(artifacts.call("runtime.project.get", project_id=project))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=project, expected_revision=project_row["revision"], grants=[]))
    denied(artifacts.call("runtime.services.execute", pipeline_id=pid, manifest_sha256=refreshed["manifest_sha256"]), "project_grant_revoked")


def test_media_rpc_unconfigured_speech_owned_frames_and_foreign_transport(artifacts):
    from agent.media_ingress import SpeechDeclaration, VoiceIngress
    capabilities = media_result(artifacts.call("runtime.media.capabilities"))
    assert not capabilities["voice"]["push_to_talk"] and capabilities["voice"]["unsupported"]
    assert not capabilities["screen"]["os_actions"] and not capabilities["screen"]["ocr"]
    denied(artifacts.call("runtime.voice.capture.start"), "speech_adapter_unconfigured")
    denied(artifacts.call("runtime.voice.speak", text="hello"), "speech_adapter_unconfigured")
    assert not media_result(artifacts.call("runtime.voice.stop"))["mission_cancelled"]
    frame = media_result(artifacts.call("runtime.screen.capture", png_base64=base64.b64encode(png()).decode(),
                                      scope="selected_window", window_ref="window-1"))
    annotation = media_result(artifacts.call("runtime.screen.annotate", frame_id=frame["frame_id"], region=[0, 0, 20, 20]))
    assert annotation["frame_sha256"] == frame["sha256"]
    denied(artifacts.call("runtime.screen.inspect", via=artifacts.peers["b"], frame_id=frame["frame_id"]))
    denied(artifacts.call("runtime.screen.capture", png_base64="bad", scope="whole_desktop", window_ref="all"))
    denied(artifacts.call("runtime.channel.bind", channel="slack"))
    class LocalSTT:
        declaration = SpeechDeclaration("fixture.local", 1, "stt", "local", True)

        def transcribe(self, pcm, *, final, cancelled):
            return "Ada at 12" if final else "Ada"

    artifacts.agents["a"]._bounded_voice_ingress = VoiceIngress(stt=LocalSTT())
    capture = media_result(artifacts.call("runtime.voice.capture.start"))["capture_id"]
    partial = media_result(artifacts.call("runtime.voice.capture.feed", capture_id=capture,
        sequence=0, pcm_base64="AAA=", final=False))
    assert partial["text"] == "Ada" and not partial["accepted_as_task"]
    final = media_result(artifacts.call("runtime.voice.capture.feed", capture_id=capture,
        sequence=1, pcm_base64="AAA=", final=True))
    assert final["text"] == "Ada at 12" and final["confirmation_required"]
    assert media_result(artifacts.call("runtime.voice.capture.cancel"))["state"] == "discarded"


def test_cross_channel_duplicate_and_confirmed_media_use_same_real_admission_queue(artifacts, monkeypatch):
    from tui_gateway import server, prompt_admission
    project = artifacts.project()["id"]
    result(artifacts.call("runtime.mission.create", mission_id="same-mission",
        contract={"outcome": "One same-work task", "project_id": project}))
    agent = artifacts.agents["a"]
    client = openai.OpenAI(api_key="test-no-live-key", base_url="https://test.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    agent.client, agent.api_mode, agent.provider = client, "chat_completions", "openai"
    monkeypatch.setattr(prompt_admission, "wake", lambda _server: None)
    try:
        local = media_result(artifacts.call("runtime.channel.bind", channel="local_jsonrpc"))
        voice = media_result(artifacts.call("runtime.channel.bind", channel="voice"))
        screen = media_result(artifacts.call("runtime.channel.bind", channel="screen"))
        assert local["mission_id"] == voice["mission_id"] and local["binding_id"] != voice["binding_id"]
        first = media_result(artifacts.call("runtime.channel.submit", binding_id=local["binding_id"], input_id="one-input",
            operation="submit", payload={"text": "Find Ada at 12"}))
        duplicate = media_result(artifacts.call("runtime.voice.submit", binding_id=voice["binding_id"], input_id="one-input",
            text="Find Ada at 12", confirmed_text="Find Ada at 12"))
        assert first["status"] == "accepted" and duplicate["command_id"] == first["command_id"]
        frame = media_result(artifacts.call("runtime.screen.capture", png_base64=base64.b64encode(png()).decode(),
            scope="selected_window", window_ref="window-1"))
        screen_duplicate = media_result(artifacts.call("runtime.screen.submit", binding_id=screen["binding_id"],
            input_id="one-input", text="Find Ada at 12", confirmed_text="Find Ada at 12",
            frame_id=frame["frame_id"], region=[0, 0, 20, 20]))
        assert screen_duplicate["command_id"] == first["command_id"]
        denied(artifacts.call("runtime.voice.submit", binding_id=voice["binding_id"], input_id="misheard",
            text="Find Ada at 12", confirmed_text="Find Adam at 120"), "media_confirmation_required")
        denied(artifacts.call("runtime.channel.submit", binding_id=local["binding_id"], input_id="one-input",
            operation="submit", payload={"text": "Different action"}), "idempotency_conflict")
        with agent._session_db._runtime_read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 1
        denied(artifacts.call("runtime.channel.submit", binding_id=local["binding_id"], input_id="history",
            operation="submit", payload={"text": "x"}, history=[{"role": "user", "content": "replay"}]))
    finally:
        for session in server._sessions.values():
            server._release_active_session_slot(session)
        client.close()


# Fixture packages are imported only by the test's controlled subprocess launch;
# the real factory/config/profile/transport paths stay in production code.
from tests.agent.test_speech_local import local_speech_packages, configured_home, _wait_for_file  # noqa: E402,F401


@pytest.mark.platforms("posix")
def test_configured_local_speech_rpc_same_client_exact_bytes_and_live_budget_denial(artifacts, local_speech_packages):
    for label, text in (("a", "Ada at 12"), ("b", "Bea at 120")):
        home = artifacts.homes[label]
        original = json.loads((home / "config.yaml").read_text())
        config = configured_home(home, text=text)
        (home / "config.yaml").write_text(json.dumps({**original, **config}))
    for label, expected in (("a", "Ada at 12"), ("b", "Bea at 120"), ("a", "Ada at 12")):
        capabilities = media_result(artifacts.call("runtime.media.capabilities", label))["voice"]
        assert capabilities["push_to_talk"] and capabilities["playback"] == "client"
        assert not capabilities["stt"]["streaming"] and not capabilities["backend_playback"]
        capture = media_result(artifacts.call("runtime.voice.capture.start", label))
        denied(artifacts.call("runtime.voice.capture.feed", label, via=artifacts.peers["b" if label == "a" else "a"],
            capture_id=capture["capture_id"], sequence=0, pcm_base64="AAA=", final=True))
        transcript = media_result(artifacts.call("runtime.voice.capture.feed", label,
            capture_id=capture["capture_id"], sequence=0, pcm_base64="AAA=", final=True))
        assert transcript["text"] == expected and transcript["confirmation_required"] and not transcript["accepted_as_task"]
        audio = media_result(artifacts.call("runtime.voice.speak", label, text=expected))
        data = base64.b64decode(audio["audio"]["pcm_base64"], validate=True)
        assert audio["audio"]["byte_length"] == len(data) and audio["audio"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert audio["audio"]["sample_rate"] == 22050 and audio["state"] == "ready"
        assert not media_result(artifacts.call("runtime.voice.stop", label))["mission_cancelled"]
        with artifacts.agents[label]._session_db._runtime_read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 0
    # A reconnect while inference runs retires the old transport. Its result
    # must be withheld even though local model computation itself succeeded.
    import threading
    from types import SimpleNamespace
    from tui_gateway import server
    home = artifacts.homes["a"]
    (home / "whisper" / "started").unlink()
    (home / "whisper" / "model.bin").write_text(json.dumps({"text": "private transcript", "wait_for_release": True}))
    capture = media_result(artifacts.call("runtime.voice.capture.start"))
    replies = []
    thread = threading.Thread(target=lambda: replies.append(artifacts.call("runtime.voice.capture.feed",
        capture_id=capture["capture_id"], sequence=0, pcm_base64="AAA=", final=True)))
    thread.start()
    _wait_for_file(home / "whisper" / "started")
    server._sessions["live-a"]["transport"] = SimpleNamespace(write=lambda _frame: True)
    (home / "whisper" / "release").touch()
    thread.join(5)
    assert not thread.is_alive() and len(replies) == 1
    denied(replies[0])
    assert "private transcript" not in json.dumps(replies[0])
    server._sessions["live-a"]["transport"] = artifacts.peers["a"]
    from agent.budget_account import parse_budget_policy
    from tests.agent.test_budget_runtime import policy
    artifacts.agents["a"]._runtime_budget_policy = parse_budget_policy({"runtime_budget": policy()})
    denied(artifacts.call("runtime.voice.speak", text="private"), "speech_budget_unsupported")
    del artifacts.agents["a"]._runtime_budget_policy
    config = json.loads((home / "config.yaml").read_text())
    config["runtime_budget"] = {"schema_version": 1}
    (home / "config.yaml").write_text(json.dumps(config))
    capabilities = media_result(artifacts.call("runtime.media.capabilities"))["voice"]
    assert set(capabilities["unsupported_reasons"].values()) == {"speech_budget_unsupported"}
    denied(artifacts.call("runtime.voice.capture.start"), "speech_budget_unsupported")
    denied(artifacts.call("runtime.voice.speak", text="private"), "speech_budget_unsupported")
    assert not media_result(artifacts.call("runtime.voice.stop"))["mission_cancelled"]
