"""The shot contract: extraction is safe, and validation fails early."""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from bookends.shot import (
    ShotError,
    discover_tarballs,
    extract_shot,
    load_shot,
    sanitise_name,
)
from tests.conftest import make_tar_with_member, make_tar_with_symlink


def test_extract_then_load(shot_factory, tar_factory, tmp_path):
    shot_dir = shot_factory("shot_001")
    tar = tar_factory(shot_dir)

    extracted = extract_shot(tar, tmp_path / "work2")
    assert extracted == tmp_path / "work2" / "shot_001"

    shot = load_shot(extracted)
    assert shot.name == "shot_001"
    assert shot.start_text.startswith("The character stands")
    assert shot.audio is not None and shot.audio.name == "audio.wav"
    assert [p.name for p in shot.refs] == ["ref_char_0.png", "ref_char_1.png"]
    assert [p.name for p in shot.plates] == ["plate_set_0.png"]
    assert shot.warnings == []


def test_use_txt_counts_as_references(shot_factory):
    """The studio exports shared images into reference/ and names them in use.txt."""
    shot_dir = shot_factory("shot_006", refs=0, plates=0)
    (shot_dir / "use.txt").write_text("toby_front.png\n", encoding="utf-8")
    shot = load_shot(shot_dir)
    assert shot.warnings == []


def test_missing_required_file_is_named(shot_factory):
    shot_dir = shot_factory("shot_002")
    (shot_dir / "stop.txt").unlink()
    with pytest.raises(ShotError) as exc:
        load_shot(shot_dir)
    assert "stop.txt" in str(exc.value)


def test_empty_required_file_is_rejected(shot_factory):
    shot_dir = shot_factory("shot_003")
    (shot_dir / "start.txt").write_text("   \n", encoding="utf-8")
    with pytest.raises(ShotError) as exc:
        load_shot(shot_dir)
    assert "start.txt is empty" in str(exc.value)


def test_parent_traversal_member_is_rejected(tmp_path):
    tar = make_tar_with_member(tmp_path / "in" / "evil.tar.gz", "../escaped.txt")
    with pytest.raises(ShotError) as exc:
        extract_shot(tar, tmp_path / "work")
    assert "escapes the extraction root" in str(exc.value)
    assert not (tmp_path / "escaped.txt").exists()


def test_absolute_member_is_rejected(tmp_path):
    tar = make_tar_with_member(tmp_path / "in" / "abs.tar.gz", "C:/windows/system32/evil.txt")
    with pytest.raises(ShotError) as exc:
        extract_shot(tar, tmp_path / "work")
    assert "absolute path" in str(exc.value)


def test_symlink_member_is_rejected(tmp_path):
    tar = make_tar_with_symlink(tmp_path / "in" / "link.tar.gz", "passwd", "/etc/passwd")
    with pytest.raises(ShotError) as exc:
        extract_shot(tar, tmp_path / "work")
    assert "link" in str(exc.value)


def test_wrapped_folder_is_flattened(shot_factory, tmp_path):
    """Tarballs made by `tar czf shot.tar.gz shot/` are the common case."""
    shot_dir = shot_factory("shot_004")
    tar = tmp_path / "in" / "shot_004.tar.gz"
    tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar, "w:gz") as fh:
        fh.add(shot_dir, arcname="shot_004")

    extracted = extract_shot(tar, tmp_path / "work")
    assert (extracted / "start.txt").is_file()
    assert not (extracted / "shot_004").is_dir()
    assert load_shot(extracted).name == "shot_004"


def test_existing_extraction_is_reused(shot_factory, tar_factory, tmp_path):
    shot_dir = shot_factory("shot_005")
    tar = tar_factory(shot_dir)
    first = extract_shot(tar, tmp_path / "work2")
    marker = first / "marker.txt"
    marker.write_text("keep me", encoding="utf-8")

    extract_shot(tar, tmp_path / "work2")  # no force
    assert marker.is_file()

    extract_shot(tar, tmp_path / "work2", force=True)  # force re-extracts
    assert not marker.exists()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("shot_001.tar.gz", "shot_001"),
        ("shot 002.tgz", "shot_002"),
        ("Shot-3.tar", "Shot-3"),
        ("weird name !!!.tar.gz", "weird_name"),
    ],
)
def test_sanitise_name(raw, expected):
    assert sanitise_name(raw) == expected


def test_sanitise_name_rejects_empty(tmp_path):
    with pytest.raises(ShotError):
        sanitise_name("!!!.tar.gz")


def test_discover_tarballs_sorted(shot_factory, tar_factory):
    for name in ("b_shot", "a_shot", "c_shot"):
        tar_factory(shot_factory(name))
    found = discover_tarballs(shot_factory("b_shot").parent.parent / "in")
    assert [p.name for p in found] == ["a_shot.tar.gz", "b_shot.tar.gz", "c_shot.tar.gz"]
