"""Staging is immutable review data; promotion never replaces parent work."""
import base64

import pytest

from tools.workspace_manifest import StagedWorkspace, WorkspaceConflict, checked_path


def envelope(path="new.txt", data=b"reviewed result"):
    return [{"path": path, "content_b64": base64.b64encode(data).decode()}]


def setup_stage(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "source.txt").write_text("base input")
    stage = StagedWorkspace.create(tmp_path / "stage", parent_root=parent,
                                   input_paths=("source.txt",), base_revision="A", new_file_targets=("new.txt",))
    return parent, stage.accept_outputs(envelope())


def promote(stage, **kwargs):
    return stage.promote_output("new.txt", current_base_revision=kwargs.get("revision", "A"),
                                 approved_manifest_digest=kwargs.get("digest", stage.manifest.digest))


def test_reviewed_additive_promotion_and_repeat_conflict(tmp_path):
    parent, stage = setup_stage(tmp_path)
    assert not (parent / "new.txt").exists()
    assert promote(stage).read_bytes() == b"reviewed result"
    with pytest.raises(WorkspaceConflict, match="target exists"):
        promote(stage)
    assert (parent / "source.txt").read_text() == "base input"


def test_stale_revision_approval_and_input_conflicts(tmp_path):
    parent, stage = setup_stage(tmp_path)
    with pytest.raises(WorkspaceConflict, match="revision"):
        promote(stage, revision="B")
    with pytest.raises(WorkspaceConflict, match="approval"):
        promote(stage, digest="wrong")
    (parent / "source.txt").write_text("edited by parent")
    with pytest.raises(WorkspaceConflict, match="parent input changed"):
        promote(stage)
    assert not (parent / "new.txt").exists()


def test_staged_output_and_input_hash_tampering(tmp_path):
    _, stage = setup_stage(tmp_path)
    output = stage.outputs / "new.txt"
    output.chmod(0o600)
    output.write_text("changed after approval")
    with pytest.raises(WorkspaceConflict, match="staged output changed"):
        promote(stage)
    source = stage.inputs / "source.txt"
    source.chmod(0o600)
    source.write_text("changed staged input")
    with pytest.raises(WorkspaceConflict, match="immutable input"):
        stage.verify_inputs()


def test_promotion_never_overwrites_parent_edits_or_symlinks(tmp_path):
    parent, stage = setup_stage(tmp_path)
    target = parent / "new.txt"
    target.write_text("parent's current work")
    with pytest.raises(WorkspaceConflict):
        promote(stage)
    assert target.read_text() == "parent's current work"
    target.unlink()
    target.symlink_to(parent / "source.txt")
    with pytest.raises(WorkspaceConflict):
        promote(stage)
    assert (parent / "source.txt").read_text() == "base input"


def test_symlink_components_and_hardlink_inputs_refused(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "real").mkdir()
    (parent / "real" / "source").write_text("original")
    (parent / "alias").symlink_to(parent / "real", target_is_directory=True)
    with pytest.raises(OSError):
        StagedWorkspace.create(tmp_path / "stage1", parent_root=parent, input_paths=("alias/source",))
    (parent / "linked").hardlink_to(parent / "real" / "source")
    with pytest.raises(WorkspaceConflict):
        StagedWorkspace.create(tmp_path / "stage2", parent_root=parent, input_paths=("linked",))


@pytest.mark.parametrize("path", ["../secret", "/absolute", "a/../b", "./a", "a//b", "a/", "a\\b", ""])
def test_noncanonical_paths_rejected(path):
    with pytest.raises(WorkspaceConflict):
        checked_path(path)


def test_output_envelope_bounds_and_duplicate_names(tmp_path):
    stage = StagedWorkspace.create(tmp_path / "stage")
    with pytest.raises(WorkspaceConflict):
        stage.accept_outputs(envelope() * 2)
    with pytest.raises(WorkspaceConflict):
        stage.accept_outputs(envelope("../escape"))
    with pytest.raises(WorkspaceConflict):
        stage.accept_outputs(envelope(data=b"x" * (2 * 1024 * 1024 + 1)))
    assert not list(stage.outputs.iterdir())


def test_undeclared_input_and_replaced_root_are_refused(tmp_path):
    stage = StagedWorkspace.create(tmp_path / "stage")
    (stage.inputs / "extra").write_text("not accepted")
    with pytest.raises(WorkspaceConflict, match="undeclared"):
        stage.verify_inputs()
    (stage.inputs / "extra").unlink()
    stage.inputs.rmdir()
    stage.inputs.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        stage.verify_inputs()


def test_existing_base_targets_are_not_reclassified_as_new(tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / 'existing').write_text('base')
    with pytest.raises(WorkspaceConflict, match='already exists'):
        StagedWorkspace.create(tmp_path / 'stage', parent_root=parent, new_file_targets=('existing',))
    stage = StagedWorkspace.create(tmp_path / 'unselected', parent_root=parent)
    stage = stage.accept_outputs(envelope('new.txt'))
    with pytest.raises(WorkspaceConflict, match='not accepted as new'):
        promote(stage, revision='empty')
