"""Scaffolding a new project: the template must arrive complete and refuse to clobber."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookends import planfile, scaffold


def test_new_project_writes_the_whole_template(tmp_path):
    dest = tmp_path / "my-film"
    result = scaffold.new_project(dest, "My Film")

    for name in ("project.json", "cast.csv", "shots.csv", "README.md"):
        assert (dest / name).is_file(), name
    assert (dest / "references").is_dir()
    assert (dest / "shots").is_dir()
    assert result["name"] == "My Film"
    assert result["size"] == [1280, 768]

    project = json.loads((dest / "project.json").read_text(encoding="utf-8"))
    assert project["name"] == "My Film"
    assert project["width"] == 1280
    assert project["height"] == 768
    assert "{{PROJECT_NAME}}" not in (dest / "README.md").read_text(encoding="utf-8")


def test_name_defaults_to_directory_name(tmp_path):
    dest = tmp_path / "the-little-escape"
    result = scaffold.new_project(dest)
    assert result["name"] == "the-little-escape"


def test_refuses_to_write_into_a_non_empty_directory(tmp_path):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep.txt").write_text("do not lose me", encoding="utf-8")

    with pytest.raises(scaffold.ScaffoldError):
        scaffold.new_project(dest)
    assert (dest / "keep.txt").read_text(encoding="utf-8") == "do not lose me"

    result = scaffold.new_project(dest, force=True)
    assert result["name"] == "occupied"
    assert (dest / "keep.txt").is_file()  # --force adds, it does not wipe


def test_rejects_off_grid_size(tmp_path):
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.new_project(tmp_path / "bad", width=1000, height=768)


def test_style_is_recorded(tmp_path):
    dest = tmp_path / "styled"
    scaffold.new_project(dest, "Styled", style="clay")
    project = json.loads((dest / "project.json").read_text(encoding="utf-8"))
    assert project["style"] == "clay"


def test_fresh_template_is_structurally_sound(tmp_path):
    """A brand-new project should only complain about the images it has not got yet."""
    dest = tmp_path / "fresh"
    scaffold.new_project(dest, "Fresh")

    issues = planfile.check(dest)
    errors = [i for i in issues if i.level == "error"]
    # The only expected errors are the reference images the example shots name.
    assert errors, "the example shots reference images that do not exist yet"
    assert all("not found in references" in i.message for i in errors), [str(e) for e in errors]
    assert planfile.summarise(issues)["shots"] == 2
