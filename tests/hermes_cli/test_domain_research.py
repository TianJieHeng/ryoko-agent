"""BE10 original bytes, live grants, exact citations and approved bounded refresh."""
from dataclasses import replace
import hashlib
import json
import time

import pytest

from agent.evidence_ledger import create_evidence_anchor
from agent.project_context import project_access
from agent.result_artifacts import ArtifactConflict, artifact_actor, read_project_artifact
from agent.source_manifest import EvidenceRange, SourceManifest, SourceManifestError, SourceRequest
from hermes_cli import projects_db as pdb
from hermes_cli.artifact_store import prepare_markdown_edit, publish_markdown
from hermes_cli.domain_research import (
    ClaimCitation, ClaimDependency, ClaimUpdate, prepare_living_brief_refresh, resolve_sources,
)
from hermes_cli.project_sources import create_capture, record_capture_extraction
from hermes_state import SessionDB
from tests.hermes_cli.test_artifact_store import artifact_runtime, approve, publish_fixture
from tools import capability_broker as broker

pytestmark = pytest.mark.platforms("linux")


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def request(project, source, content, quote, **kwargs):
    data, exact = content.encode(), quote.encode()
    start = data.index(exact)
    return SourceRequest(source["artifact_id"], "project_artifact", project, source["version"],
                         source["sha256"], evidence_ranges=(EvidenceRange(start, start + len(exact), sha(exact), quote),), **kwargs)


def test_original_resolvers_citations_authority_and_reopen_use_real_publication(artifact_runtime):
    r = artifact_runtime
    content = "# Source\nαβ Approved limit 10.\n"
    with r.scope() as run:
        spec = publish_fixture(run, r.project, "spec", content)
        extracted = publish_fixture(run, r.project, "extracted", "# Extracted\nNot the original\n")
        capture = create_capture(run.context, r.db, project_id=r.project,
            original_ref={"artifact_id": spec["artifact_id"], "version": spec["version"]},
            source_url="https://metadata.invalid/must-not-fetch", annotation="Casual message")
        record_capture_extraction(run.context, r.db, capture["capture_id"], status="succeeded",
                                  extracted_ref={"artifact_id": extracted["artifact_id"], "version": extracted["version"]})
        first = request(r.project, spec, content, "αβ Approved limit 10.", authority="authoritative_spec")
        span = first.evidence_ranges[0]
        anchor = create_evidence_anchor(run.context, r.db, project_id=r.project, kind="source_span",
            source_ref={"artifact_id": spec["artifact_id"], "version": spec["version"]},
            source_version=str(spec["version"]), range_ref={"unit": "byte", "start": span.start, "end": span.end},
            authority="user_approved", validity="current")
        first = replace(first, evidence_ranges=(replace(span, anchor_id=anchor["anchor_id"]),))
        second = replace(first, source_id=capture["capture_id"], source_type="capture_original", authority="casual_note",
                         evidence_ranges=(span,), fresh_until=time.time() + 60)
        result = resolve_sources(run.context, r.db, (first, second))
        assert [source.content_bytes for source in result.sources] == [content.encode()] * 2
        record = json.loads(result.to_bytes())
        assert record["complete"] and record["coverage"]["available"] == 2
        assert record["duplicate_groups"] == []  # Equal bytes do not flatten authority classes.
        assert record["validator_manifest"]["claim_verification"] == "not_performed"
        assert record["validator_manifest"]["live_two_source_acceptance"] == "pending_P08"
        assert result.sources[1].manifest.acquired_at == capture["acquired_at"]
        assert record["sources"][0]["covered_bytes"] == len(span.quote.encode())
        assert SourceManifest.from_record(record["sources"][0]) == result.sources[0].manifest
        assert "locator" not in result.to_bytes().decode()
        report = publish_fixture(run, r.project, "research-output", "# Research\n\n" + result.to_bytes().decode(),
                                 derived_from=[{"artifact_id": spec["artifact_id"], "version": spec["version"]}])
        with SessionDB(r.home / "state.db") as reopened:
            assert read_project_artifact(run.context, reopened, r.project, report["artifact_id"], report["version"]).startswith(b"# Research")
            assert resolve_sources(run.context, reopened, (first, second)).sources[1].content_bytes == content.encode()


def test_stale_missing_revoked_and_changed_bytes_never_claim_complete_evidence(artifact_runtime):
    r = artifact_runtime
    with r.scope() as run:
        content = "# Spec\nLimit 10\n"
        original = publish_fixture(run, r.project, "spec", content)
        exact = request(r.project, original, content, "Limit 10")
        publish_fixture(run, r.project, "new-version", content.replace("10", "20"),
                        artifact_id=original["artifact_id"], parent_version=original["version"])
        stale = resolve_sources(run.context, r.db, (exact,))
        assert stale.sources[0].content_bytes == content.encode()
        assert stale.sources[0].manifest.freshness == "stale"
        assert stale.to_record()["validator_manifest"]["status"] == "partial"
        missing = resolve_sources(run.context, r.db, (replace(exact, source_id="artifact_missing"),))
        assert missing.sources[0].manifest.availability == "missing"
        assert missing.sources[0].content_bytes is None
        row = r.db.read_artifact_version(original["artifact_id"], original["version"],
                                        artifact_actor(run.context), access=project_access(run.context))
        blob = r.home / row["descriptor"]["locator"]
        blob.unlink()
        assert resolve_sources(run.context, r.db, (exact,)).sources[0].manifest.availability == "missing"
        blob.write_bytes(b"tampered")
        invalid = resolve_sources(run.context, r.db, (exact,))
        assert invalid.sources[0].manifest.errors == ("source_bytes_invalid",)
        with pdb.connect_closing() as conn:
            conn.execute("DELETE FROM project_grants WHERE project_id=?", (r.project,))
            conn.commit()
        revoked = resolve_sources(run.context, r.db, (exact,))
        assert revoked.sources[0].manifest.availability == "inaccessible"
        assert revoked.sources[0].content_bytes is None
        assert not revoked.to_record()["complete"]


@pytest.mark.parametrize("fault", ["quote", "range", "digest", "anchor", "revoked_anchor"])
def test_false_citations_and_revoked_evidence_are_rejected(artifact_runtime, fault):
    r = artifact_runtime
    with r.scope() as run:
        content = "# Fact\nα real statement\n"
        source = publish_fixture(run, r.project, "fact", content)
        exact = request(r.project, source, content, "real statement")
        span = exact.evidence_ranges[0]
        if fault == "quote":
            span = replace(span, quote="a false claim")
        elif fault == "range":
            span = replace(span, start=span.start + 1, end=span.end + 1)
        elif fault == "digest":
            span = replace(span, sha256="0" * 64)
        else:
            anchor = create_evidence_anchor(run.context, r.db, project_id=r.project, kind="source_span",
                source_ref={"artifact_id": source["artifact_id"], "version": source["version"]},
                source_version=str(source["version"]),
                range_ref={"unit": "byte", "start": span.start, "end": span.end if fault == "revoked_anchor" else span.end - 1},
                validity="revoked" if fault == "revoked_anchor" else "current")
            span = replace(span, anchor_id=anchor["anchor_id"])
        result = resolve_sources(run.context, r.db, (replace(exact, evidence_ranges=(span,)),))
        assert result.sources[0].manifest.availability == "invalid"
        assert result.sources[0].content_bytes is None
        assert not result.to_record()["complete"]


def brief_inputs(run, r):
    spec_text = "# Source\nThe limit is 10.\n"
    original = publish_fixture(run, r.project, "spec", spec_text)
    old_request = request(r.project, original, spec_text, "10", authority="authoritative_spec")
    previous = resolve_sources(run.context, r.db, (old_request,))
    content = "# Facts\nThe limit is 10.\n# Advice\nTry ten items.\n# User\nMy original note.\n# Locked\nKeep exactly.\n"
    brief = publish_fixture(run, r.project, "brief", content, locked_sections=["Locked"])
    claims = (
        ClaimDependency("limit", "Facts", "The limit is 10.", "fact", sha("# Facts\nThe limit is 10.\n"),
                        (ClaimCitation(original["artifact_id"], 0),)),
        ClaimDependency("recommendation", "Advice", "Try ten items.", "interpretation", sha("# Advice\nTry ten items.\n"),
                        (ClaimCitation(original["artifact_id"], 0),)),
    )
    updated_source = publish_fixture(run, r.project, "source-change", spec_text.replace("10", "20"),
                                     artifact_id=original["artifact_id"], parent_version=original["version"])
    updated_request = request(r.project, updated_source, spec_text.replace("10", "20"), "20", authority="authoritative_spec")
    return content, brief, previous, updated_request, claims


def test_controlled_refresh_preserves_user_edits_locks_history_and_separates_interpretation(artifact_runtime):
    r = artifact_runtime
    with r.scope() as run:
        content, brief, previous, incoming, claims = brief_inputs(run, r)
        user_edit = prepare_markdown_edit(run, project_id=r.project, artifact_id=brief["artifact_id"],
            parent_version=brief["version"], request_id="user-edit",
            edits=[{"anchor": "User", "expected_sha256": sha("# User\nMy original note.\n"),
                    "replacement": "# User\nMy handwritten correction.\n"}])
        approve(user_edit)
        current = publish_markdown(run, user_edit)
        refreshed = prepare_living_brief_refresh(run, project_id=r.project, artifact_id=brief["artifact_id"],
            parent_version=current["version"], request_id="refresh",
            previous_sources=tuple(source.manifest for source in previous.sources), requests=(incoming,), claims=claims,
            updates=(ClaimUpdate("limit", "The limit is 20."), ClaimUpdate("recommendation", "Try twenty items.")))
        assert refreshed.factual_changes == ("limit",)
        assert refreshed.interpretation_changes == ("recommendation",)
        assert refreshed.to_record()["claim_verification"] == "not_performed"
        with pytest.raises(broker.CapabilityDenied, match="approved"):
            publish_markdown(run, refreshed.proposal)
        approve(refreshed.proposal)
        published = publish_markdown(run, refreshed.proposal)
        actual = read_project_artifact(run.context, r.db, r.project, published["artifact_id"], published["version"]).decode()
        expected = content.replace("My original note.", "My handwritten correction.").replace("is 10", "is 20").replace("ten items", "twenty items")
        assert actual == expected
        assert read_project_artifact(run.context, r.db, r.project, brief["artifact_id"], brief["version"]).decode() == content
        assert refreshed.dependencies[0].section_sha256 == sha("# Facts\nThe limit is 20.\n")


def test_refresh_refuses_user_edited_claim_section_unchanged_evidence_and_locked_section(artifact_runtime):
    r = artifact_runtime
    with r.scope() as run:
        content, brief, previous, incoming, claims = brief_inputs(run, r)
        old = previous.sources[0].manifest
        common = dict(project_id=r.project, artifact_id=brief["artifact_id"], parent_version=brief["version"],
                      previous_sources=(old,), claims=claims, updates=(ClaimUpdate("limit", "The limit is 20."),))
        with pytest.raises(SourceManifestError, match="Only claims"):
            prepare_living_brief_refresh(run, request_id="no-change", requests=(old.request(),), **common)
        forged = replace(old, evidence_ranges=(replace(old.evidence_ranges[0], quote="99"),))
        with pytest.raises(SourceManifestError, match="Prior source"):
            prepare_living_brief_refresh(run, request_id="forged", requests=(incoming,),
                                        **{**common, "previous_sources": (forged,)})
        changed = publish_fixture(run, r.project, "user-facts", content.replace("is 10", "is personally adjusted"),
                                  artifact_id=brief["artifact_id"], parent_version=brief["version"])
        with pytest.raises(SourceManifestError, match="User-edited"):
            prepare_living_brief_refresh(run, request_id="overwrite", requests=(incoming,),
                                        **{**common, "parent_version": changed["version"]})
        locked = ClaimDependency("locked", "Locked", "Keep exactly.", "interpretation", sha("# Locked\nKeep exactly.\n"), claims[0].citations)
        with pytest.raises(ArtifactConflict, match="locked"):
            prepare_living_brief_refresh(run, request_id="locked", requests=(incoming,),
                **{**common, "claims": (locked,), "updates": (ClaimUpdate("locked", "Replace it."),)})


def test_partial_refresh_retains_exact_old_dependency_for_later_recommendation(artifact_runtime):
    r = artifact_runtime
    with r.scope() as run:
        content, brief, previous, incoming, claims = brief_inputs(run, r)
        partial = prepare_living_brief_refresh(run, project_id=r.project, artifact_id=brief["artifact_id"],
            parent_version=brief["version"], request_id="facts-first",
            previous_sources=tuple(source.manifest for source in previous.sources), requests=(incoming,), claims=claims,
            updates=(ClaimUpdate("limit", "The limit is 20."),))
        assert partial.to_record()["pending_claims"] == ["recommendation"]
        restored = tuple(ClaimDependency.from_record(item) for item in json.loads(json.dumps(partial.to_record()))["claim_dependencies"])
        assert restored == partial.dependencies
        assert partial.dependencies[0].citations[0].source_version == incoming.version
        assert partial.dependencies[1].citations[0].source_version == previous.sources[0].manifest.version
        approve(partial.proposal)
        first = publish_markdown(run, partial.proposal)
        second = prepare_living_brief_refresh(run, project_id=r.project, artifact_id=brief["artifact_id"],
            parent_version=first["version"], request_id="recommendation-later",
            previous_sources=partial.dependency_sources, requests=(incoming,), claims=partial.dependencies,
            updates=(ClaimUpdate("recommendation", "Try twenty items."),))
        assert second.factual_changes == ()
        assert second.interpretation_changes == ("recommendation",)
        assert second.to_record()["pending_claims"] == []
        assert len(second.dependency_sources) == 1
        approve(second.proposal)
        final = publish_markdown(run, second.proposal)
        assert read_project_artifact(run.context, r.db, r.project, final["artifact_id"], final["version"]).decode() == content.replace("is 10", "is 20").replace("ten items", "twenty items")


def test_resolvers_follow_live_profile_a_b_a_without_cross_store_reads(artifact_runtime, tmp_path, monkeypatch):
    from agent.artifact_commands import finish_artifact_control
    from tests.hermes_cli.test_artifact_store import artifact_runtime as make_runtime
    r = artifact_runtime
    with r.scope("profile-a-produce") as run:
        first = publish_fixture(run, r.project, "same-id", "# A\nProfile A source\n", artifact_id="same-source")
        request_a = request(r.project, first, "# A\nProfile A source\n", "Profile A source")
        assert resolve_sources(run.context, r.db, (request_a,)).sources[0].content_bytes == b"# A\nProfile A source\n"
        finish_artifact_control(run, first)
    home_b = tmp_path / "profile-b"
    home_b.mkdir()
    with monkeypatch.context() as patch:
        fixture = make_runtime.__wrapped__(home_b, patch)
        other = next(fixture)
        try:
            with other.scope("profile-b-produce") as run:
                second = publish_fixture(run, other.project, "same-id", "# B\nProfile B source\n", artifact_id="same-source")
                request_b = request(other.project, second, "# B\nProfile B source\n", "Profile B source")
                assert resolve_sources(run.context, other.db, (request_b,)).sources[0].content_bytes == b"# B\nProfile B source\n"
                with pytest.raises(ValueError, match="owning profile"):
                    resolve_sources(run.context, r.db, (request_a,))
                finish_artifact_control(run, second)
        finally:
            next(fixture, None)
    with r.scope("profile-a-read") as run:
        assert resolve_sources(run.context, r.db, (request_a,)).sources[0].content_bytes == b"# A\nProfile A source\n"
        finish_artifact_control(run, {"read": True})


def test_expired_source_and_unchanged_claims_stay_visible_without_rewriting(artifact_runtime):
    r = artifact_runtime
    with r.scope() as run:
        content, brief, previous, incoming, claims = brief_inputs(run, r)
        stale = replace(incoming, fresh_until=time.time() - 1)
        resolved = resolve_sources(run.context, r.db, (stale,))
        assert resolved.sources[0].manifest.freshness == "stale"
        with pytest.raises(SourceManifestError, match="available current"):
            prepare_living_brief_refresh(run, project_id=r.project, artifact_id=brief["artifact_id"],
                parent_version=brief["version"], request_id="expired-evidence",
                previous_sources=tuple(source.manifest for source in previous.sources), requests=(stale,), claims=claims,
                updates=(ClaimUpdate("limit", "The limit is 20."),))
        assert read_project_artifact(run.context, r.db, r.project, brief["artifact_id"], brief["version"]).decode() == content


def test_changed_range_selection_same_version_keeps_unmodified_claim_manifest(artifact_runtime):
    r = artifact_runtime
    with r.scope() as run:
        source_text = "# Spec\nLegacy 10, current 20\n"
        source = publish_fixture(run, r.project, "source", source_text)
        old_request = request(r.project, source, source_text, "10")
        incoming = request(r.project, source, source_text, "20")
        old_sources = resolve_sources(run.context, r.db, (old_request,))
        brief = publish_fixture(run, r.project, "brief", "# Fact\nUse 10.\n# Advice\nTry ten.\n")
        claims = (ClaimDependency("fact", "Fact", "Use 10.", "fact", sha("# Fact\nUse 10.\n"), (ClaimCitation(source["artifact_id"], 0),)),
                  ClaimDependency("advice", "Advice", "Try ten.", "interpretation", sha("# Advice\nTry ten.\n"), (ClaimCitation(source["artifact_id"], 0),)))
        first = prepare_living_brief_refresh(run, project_id=r.project, artifact_id=brief["artifact_id"],
            parent_version=brief["version"], request_id="fact-range-change", previous_sources=tuple(item.manifest for item in old_sources.sources),
            requests=(incoming,), claims=claims, updates=(ClaimUpdate("fact", "Use 20."),))
        assert len(first.dependency_sources) == 2
        assert first.dependencies[0].citations[0].request_digest != first.dependencies[1].citations[0].request_digest
        approve(first.proposal)
        published = publish_markdown(run, first.proposal)
        second = prepare_living_brief_refresh(run, project_id=r.project, artifact_id=brief["artifact_id"],
            parent_version=published["version"], request_id="advice-range-change", previous_sources=first.dependency_sources,
            requests=(incoming,), claims=first.dependencies, updates=(ClaimUpdate("advice", "Try twenty."),))
        assert second.affected_claims == ("advice",)
        assert len(second.dependency_sources) == 1
