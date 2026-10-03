"""A prepared dependency cache is read-only input to isolated PM fixtures."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pm._fixtures import seed_pm_runtime_cache


def _request(cache):
    return SimpleNamespace(config=SimpleNamespace(getini=lambda name: str(cache)))


def test_prepared_cache_is_copied_without_sharing_mutable_files(tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    archive = source / "archive"
    archive.mkdir()
    (archive / "metadata").write_text("original", encoding="utf-8")
    (source / "wheel").symlink_to("archive", target_is_directory=True)
    destination = tmp_path / "working"

    assert seed_pm_runtime_cache(_request(source), destination)
    assert (destination / "wheel").resolve() == destination / "archive"
    copied = destination / "wheel" / "metadata"
    assert not copied.samefile(archive / "metadata")
    copied.write_text("changed by uv", encoding="utf-8")
    assert (archive / "metadata").read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("absolute", [False, True])
def test_prepared_cache_rejects_links_outside_its_tree_before_copy(tmp_path, absolute):
    source = tmp_path / "prepared"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("must stay untouched", encoding="utf-8")
    (source / "wheel").symlink_to(outside if absolute else Path("../outside"))
    destination = tmp_path / "working"

    with pytest.raises(AssertionError, match="link escapes its tree"):
        seed_pm_runtime_cache(_request(source), destination)
    assert not destination.exists()
    assert outside.read_text(encoding="utf-8") == "must stay untouched"
