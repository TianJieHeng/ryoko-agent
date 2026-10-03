import hashlib
import json
import time

import pytest

from hermes_cli.domain_media import parse_transcript, build_meeting_package, build_creative_package
from tests.hermes_cli.test_artifact_store import artifact_runtime, approve  # noqa: F401


def publish(run, project, name, data, mime):
    from hermes_cli.artifact_store import prepare_artifact, publish_artifact
    proposal = prepare_artifact(run, project_id=project, request_id=name, content_bytes=data, mime=mime)
    approve(proposal)
    result = publish_artifact(run, proposal)
    return {key: result[key] for key in ("artifact_id", "version", "sha256")}


def test_supplied_transcript_preserves_unknown_speaker_gaps_and_uncertainty():
    parsed = parse_transcript(b"WEBVTT\n\n00:01.000 --> 00:02.000\nMaybe next week.\n", "text/vtt")
    assert parsed["segments"][0]["speaker"] is None
    assert parsed["segments"][0]["confidence"] == "unknown"
    assert parsed["gaps"] == [{"start_ms": 0, "end_ms": 1000, "status": "unobserved"}]
    assert parsed["semantic_accuracy"] == "unverified"


@pytest.mark.parametrize("data", [b"1\n00:70:00,000 --> 00:71:00,000\na", b"1\n00:00:02,000 --> 00:00:01,000\na"])
def test_subtitle_timing_cannot_be_faked(data):
    with pytest.raises(ValueError):
        parse_transcript(data, "application/x-subrip")


def test_real_source_consent_proposed_outcome_and_complete_package(artifact_runtime):
    from agent.result_artifacts import read_project_artifact
    runtime = artifact_runtime
    with runtime.scope() as run:
        raw = b'{"schema_version":1,"segments":[{"start_ms":0,"end_ms":2000,"text":"Maybe I can send it next week","speaker":null,"confidence":"uncertain"}]}'
        ref = publish(run, runtime.project, "transcript", raw, "application/json")
        consent = publish(run, runtime.project, "consent", json.dumps({"operation": "supplied_transcript_processing",
            "consent": True, "source_sha256": ref["sha256"]}).encode(), "application/json")
        package = build_meeting_package(run.context, run.db, project_id=runtime.project, transcript_ref=ref,
            consent_ref=consent, retention_until=time.time() + 86400,
            outcomes=[{"text": "Possible delivery next week", "kind": "commitment_proposal", "segment_ids": [0]}],
            speaker_corrections={"0": "speaker-A"})
        assert package["metadata"]["outcomes"][0]["accepted_obligation"] is False
        assert package["metadata"]["transcript"]["segments"][0]["original_speaker"] is None
        result = publish(run, runtime.project, "meeting", package["content_bytes"], package["mime"])
        assert read_project_artifact(run.context, run.db, runtime.project, result["artifact_id"], result["version"]) == package["content_bytes"]
        bad = {**ref, "sha256": hashlib.sha256(b"different").hexdigest()}
        with pytest.raises(ValueError, match="Consent"):
            build_meeting_package(run.context, run.db, project_id=runtime.project, transcript_ref=bad,
                consent_ref=consent, retention_until=time.time() + 86400)


def test_creative_prompt_package_never_claims_generated_assets(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        package = build_creative_package(run.context, run.db, project_id=runtime.project,
            brief="A short scene", prompts=["Blue doorway at dawn"], continuity=["Door stays blue"])
        assert package["metadata"]["stage"] == "prompt_only"
        assert package["metadata"]["production_status"] == "awaiting_production"
        assert package["metadata"]["validator_manifest"]["generated_assets"] == 0
        with pytest.raises(ValueError, match="generated"):
            build_creative_package(run.context, run.db, project_id=runtime.project, brief="Scene",
                prompts=["Door"], assets=[{"name": "frame.png", "ref": {}, "rights": "owned", "stage": "generated"}])
