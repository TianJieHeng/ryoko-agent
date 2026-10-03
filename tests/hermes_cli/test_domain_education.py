"""BE10 F14/F15 acceptance: real owned project artifacts and verified arithmetic."""
from fractions import Fraction
import json

import pytest

from agent.result_artifacts import ArtifactConflict, read_project_artifact
from hermes_cli.artifact_store import _section, _sections, prepare_markdown, publish_markdown
from hermes_cli import projects_db
from hermes_cli.domain_education import (
    EducationError, build_educator_package, build_tutor_package,
    prepare_education_package, publish_education_package,
)
from hermes_state import SessionDB
from tests.hermes_cli.test_artifact_store import artifact_runtime, approve, publish_fixture
from tools import capability_broker as broker

pytestmark = pytest.mark.platforms("linux")


def ref(record):
    return {key: record[key] for key in ("artifact_id", "version", "sha256")}


def save(run, project, request, package):
    proposal = prepare_education_package(run, project_id=project, request_id=request, package=package)
    approve(proposal)
    published = publish_education_package(run, proposal)
    assert published["reopen_validation"]["status"] == "passed"
    assert published["reopen_validation"]["size"] == len(package["content_bytes"])
    assert read_project_artifact(run.context, run.db, project, published["artifact_id"], published["version"]) == package["content_bytes"]
    return ref(published)


def test_tutor_real_versioned_history_hint_revisit_reset_and_held_out_transfer(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        source = publish_fixture(run, runtime.project, "context", "# Goal\nUse exact rational arithmetic.\n")
        initial = build_tutor_package(run.context, run.db, project_id=runtime.project,
            goal="Add and scale fractional quantities", source_refs=[ref(source)])
        assert initial["metadata"]["source_refs"] == [ref(source)]
        assert b"t1" not in initial["content_bytes"] and b"11/8" not in initial["content_bytes"]
        proposed = prepare_education_package(run, project_id=runtime.project, request_id="begin", package=initial)
        with pytest.raises(broker.CapabilityDenied):
            publish_education_package(run, proposed)
        approve(proposed)
        first = publish_education_package(run, proposed)
        current = ref(first)
        snapshots = []

        def step(action, stamp):
            nonlocal current
            package = build_tutor_package(run.context, run.db, project_id=runtime.project,
                prior_ref=current, action=action, now=stamp)
            current = save(run, runtime.project, f"step-{len(snapshots)}", package)
            snapshots.append(package)
            return package["metadata"]["state"]

        state = step({"type": "hint"}, 1000)
        assert state["active"]["hints"] == 1
        assert b"Hint 1:" in snapshots[-1]["content_bytes"] and b"Hint 2:" not in snapshots[-1]["content_bytes"]
        state = step({"type": "answer", "answer": "10/14"}, 1001)
        assert state["history"][-1]["correct"] is True
        assert state["difficulty"] == 2 and state["active"]["exercise_id"] == "p4"
        state = step({"type": "answer", "answer": "1"}, 1002)
        assert state["difficulty"] == 1 and state["history"][-1]["correct"] is False
        state = step({"type": "choose_revisit", "exercise_id": "p1", "days": 1}, 1003)
        assert state["revisits"][0]["due_at"] == 87403
        with pytest.raises(EducationError, match="due revisit"):
            build_tutor_package(run.context, run.db, project_id=runtime.project, prior_ref=current,
                action={"type": "start_revisit", "exercise_id": "p1"}, now=2000)
        state = step({"type": "start_revisit", "exercise_id": "p1"}, 87403)
        assert state["active"]["mode"] == "revisit"
        state = step({"type": "answer", "answer": "5/7"}, 87404)
        assert not state["revisits"]
        state = step({"type": "set_difficulty", "difficulty": 3}, 87405)
        assert state["difficulty"] == 3
        state = step({"type": "start_transfer"}, 87406)
        assert state["active"]["exercise_id"] == "t1"
        assert b"11/8" not in snapshots[-1]["content_bytes"]
        with pytest.raises(EducationError, match="hints"):
            build_tutor_package(run.context, run.db, project_id=runtime.project, prior_ref=current,
                action={"type": "hint"}, now=87407)
        state = step({"type": "answer_transfer", "answer": "1.375"}, 87408)
        assert state["history"][-1]["correct"] is True and state["difficulty"] == 3
        assert state["history"][-1]["mode"] == "transfer"
        assert state["active"]["exercise_id"] != "t1"
        with SessionDB(runtime.home / "state.db") as reopened:
            reopened_package = build_tutor_package(run.context, reopened, project_id=runtime.project, prior_ref=current)
            assert reopened_package["metadata"]["state"] == state
        state = step({"type": "reset", "confirmed": True}, 87409)
        assert state["difficulty"] == 1 and state["active"]["mode"] == "diagnostic"
        assert state["history"] and state["transfer_exposed"] == ["t1"]
        with pytest.raises(EducationError, match="already exposed"):
            build_tutor_package(run.context, run.db, project_id=runtime.project, prior_ref=current,
                action={"type": "start_transfer"}, now=87410)
        with pytest.raises(EducationError, match="1 to 3"):
            build_tutor_package(run.context, run.db, project_id=runtime.project, prior_ref=current,
                action={"type": "set_difficulty", "difficulty": 4})
        assert read_project_artifact(run.context, run.db, runtime.project, first["artifact_id"], first["version"]) == initial["content_bytes"]
        assert snapshots[-1]["metadata"]["validator_manifest"]["state_replay"] == "passed"
        json.dumps(snapshots[-1]["metadata"], allow_nan=False)


def test_tutor_fails_closed_on_forged_state_sources_and_revoked_authority(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        source = publish_fixture(run, runtime.project, "source", "# Material\nFraction rules.\n")
        with pytest.raises(EducationError, match="reference"):
            build_tutor_package(run.context, run.db, project_id=runtime.project, prior_ref={})
        with pytest.raises(EducationError, match="digest"):
            build_tutor_package(run.context, run.db, project_id=runtime.project,
                source_refs=[{**ref(source), "sha256": "0" * 64}])
        package = build_tutor_package(run.context, run.db, project_id=runtime.project,
            source_refs=[ref(source)], action={"type": "answer", "answer": "0"}, now=1000)
        text = package["content_bytes"].decode()
        prefix, payload = text.split("<!-- education-state-v1 -->\n")
        state = json.loads(payload)
        state["state"]["history"][0]["correct"] = True
        forged = publish_fixture(run, runtime.project, "forged", prefix + "<!-- education-state-v1 -->\n" + json.dumps(state))
        with pytest.raises(EducationError, match="independent event replay"):
            build_tutor_package(run.context, run.db, project_id=runtime.project, prior_ref=ref(forged))
        for invalid in ({"type": "answer", "answer": "nan"}, {"type": "answer", "answer": "1/0"},
                        {"type": "reset", "confirmed": False}, {"type": "set_difficulty", "difficulty": True},
                        {"type": "answer", "answer": "5/7", "correct": True}, {"type": []}):
            with pytest.raises(EducationError):
                build_tutor_package(run.context, run.db, project_id=runtime.project, action=invalid)
        with projects_db.connect_closing() as conn:
            conn.execute("DELETE FROM project_grants WHERE project_id=?", (runtime.project,))
            conn.commit()
        with pytest.raises(PermissionError):
            build_tutor_package(run.context, run.db, project_id=runtime.project, source_refs=[ref(source)])


def test_educator_alignment_exact_answers_and_dependent_only_locked_revision(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        source = publish_fixture(run, runtime.project, "source", "# Audience\nSmall-group fraction practice.\n")
        package = build_educator_package(run.context, run.db, project_id=runtime.project,
            audience="Adult learners refreshing arithmetic", duration_minutes=30, source_refs=[ref(source)])
        first = save(run, runtime.project, "lesson", package)
        initial = package["content_bytes"].decode()
        assert b"Prerequisites:" in package["content_bytes"]
        assert package["metadata"]["validator_manifest"]["alignment"] == "passed"
        assert package["metadata"]["validator_manifest"]["docx_pptx"] == "not_supported"
        # An actual instructor edit is published as a new immutable version, then
        # locked by BE07. Revision must retain its exact bytes and other objectives.
        anchor = "Teaching material same_denominator"
        old = _section(_sections(initial), anchor)[2]
        edited = initial.replace(old, old.replace("Model the method", "Instructor edit: use paper fraction strips.\nModel the method"))
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="instructor-edit",
            artifact_id=first["artifact_id"], parent_version=first["version"], content=edited,
            locked_sections=[anchor, "Exercise same_denominator"])
        approve(proposal)
        teacher_version = ref(publish_markdown(run, proposal))
        revised = build_educator_package(run.context, run.db, project_id=runtime.project, prior_ref=teacher_version,
            changed_objective_ids=["unlike_denominators"],
            objective_updates={"unlike_denominators": {"exercise_id": "p5", "activity_minutes": 10}})
        second = save(run, runtime.project, "revise-dependent", revised)
        actual = revised["content_bytes"].decode()
        before, after = _sections(edited), _sections(actual)
        for heading in before:
            if heading not in revised["metadata"]["changed_sections"]:
                assert before[heading][2] == after[heading][2]
        assert _section(after, anchor)[2] == _section(before, anchor)[2]
        assert revised["metadata"]["state"]["duration_minutes"] == 32
        assert "Exercise ID: p5\n" in _section(after, "Answer key unlike_denominators")[2]
        assert Fraction("7/12") == Fraction("5/6") - Fraction("1/4")
        assert read_project_artifact(run.context, run.db, runtime.project, first["artifact_id"], first["version"]) == package["content_bytes"]
        with pytest.raises(ArtifactConflict, match="locked"):
            build_educator_package(run.context, run.db, project_id=runtime.project, prior_ref=second,
                objective_updates={"same_denominator": {"exercise_id": "p2"}})
        with pytest.raises(EducationError, match="align"):
            build_educator_package(run.context, run.db, project_id=runtime.project, prior_ref=second,
                objective_updates={"unlike_denominators": {"exercise_id": "p7"}})
        wrong = actual.replace("Answer: 7/12\n", "Answer: 1/12\n")
        forged = publish_fixture(run, runtime.project, "wrong-answer-key", wrong)
        with pytest.raises(EducationError, match="answer-key"):
            build_educator_package(run.context, run.db, project_id=runtime.project, prior_ref=ref(forged))
        wrong_coverage = actual.replace("p5 in [Exercise unlike_denominators]", "p7 in [Exercise unlike_denominators]")
        forged_coverage = publish_fixture(run, runtime.project, "wrong-coverage", wrong_coverage)
        with pytest.raises(EducationError, match="coverage"):
            build_educator_package(run.context, run.db, project_id=runtime.project, prior_ref=ref(forged_coverage))
        with pytest.raises(EducationError, match="duration"):
            build_educator_package(run.context, run.db, project_id=runtime.project, prior_ref=second,
                objective_updates={"unlike_denominators": {"activity_minutes": 115}})


def test_learner_artifacts_remain_owned_across_two_profile_scopes(artifact_runtime, tmp_path, monkeypatch):
    from hermes_cli.project_sources import ProjectSourceError
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    a = artifact_runtime
    other_home = tmp_path / "second-profile"
    other_home.mkdir()
    with a.scope() as arun:
        apackage = build_tutor_package(arun.context, a.db, project_id=a.project, goal="Profile A fraction practice")
        aref = save(arun, a.project, "profile-a", apackage)
        with monkeypatch.context() as patch:
            # The fixture value is the runtime; instantiate its actual fixture
            # function under an isolated monkeypatch scope for a separate profile.
            from tests.hermes_cli.test_artifact_store import artifact_runtime as fixture_function
            generator = fixture_function.__wrapped__(other_home, patch)
            token = set_hermes_home_override(other_home)
            try:
                b = next(generator)
            finally:
                reset_hermes_home_override(token)
            try:
                with b.scope() as brun:
                    bpackage = build_tutor_package(brun.context, b.db, project_id=b.project, goal="Profile B fraction practice")
                    bref = save(brun, b.project, "profile-b", bpackage)
                    assert bref["artifact_id"] != aref["artifact_id"]
                    with pytest.raises(ProjectSourceError, match="owning profile"):
                        build_tutor_package(brun.context, a.db, project_id=b.project, prior_ref=aref)
                    assert bpackage["metadata"]["scope"]["project_id"] == b.project
            finally:
                with pytest.raises(StopIteration):
                    next(generator)
        resumed = build_tutor_package(arun.context, a.db, project_id=a.project, prior_ref=aref)
        assert resumed["content_bytes"] == apackage["content_bytes"]
        assert b"Profile B" not in resumed["content_bytes"]
