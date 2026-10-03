"""Bounded typed source records cannot promote labels into authority or truth."""
import json

import pytest

from agent.source_manifest import EvidenceRange, SourceManifestError, SourceRequest


@pytest.mark.parametrize("change", [
    {"version": True}, {"source_id": "*"}, {"source_type": "session_search"},
    {"source_type": "url"}, {"source_type": []}, {"authority": "approved_execution"},
    {"sha256": "ab"}, {"fresh_until": float("nan")}, {"source_url": "https://invalid.test"},
    {"evidence_ranges": [{"start": 0, "end": 99, "sha256": "0" * 64, "quote": "x" * 8193}]},
])
def test_source_requests_reject_unbounded_or_unsupported_claimed_authority(change):
    record = {"source_id": "artifact_a", "source_type": "project_artifact", "project_id": "project_a",
              "version": 1, "sha256": "0" * 64, **change}
    with pytest.raises(SourceManifestError):
        SourceRequest.from_record(record)


def test_exact_ranges_roundtrip_and_mutable_collections_are_not_typed_records():
    span = EvidenceRange(7, 9, "0" * 64, "α")
    request = SourceRequest("artifact_a", "project_artifact", "project_a", 1, "1" * 64,
                            "authoritative_spec", (span,))
    assert SourceRequest.from_record(json.loads(json.dumps(request.to_record()))) == request
    with pytest.raises(SourceManifestError):
        SourceRequest("artifact_a", "project_artifact", "project_a", 1, "1" * 64,
                      evidence_ranges=[span])
