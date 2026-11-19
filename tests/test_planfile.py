"""Plan validation: every error here is one that would otherwise surface on a paid GPU."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bookends import planfile, scaffold
from tests.conftest import write_image


def make_project(tmp_path: Path, rows: list[dict] | None = None, *, project: dict | None = None) -> Path:
    dest = tmp_path / "film"
    scaffold.new_project(dest, "Film")
    if project:
        (dest / "project.json").write_text(json.dumps(project, indent=2), encoding="utf-8")
    if rows is not None:
        with open(dest / "shots.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(planfile.SHOT_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
    return dest


def good_row(**overrides) -> dict:
    row = {
        "shot": "001",
        "scene": "1",
        "duration_s": "7.0",
        "start_text": "A girl in a red raincoat stands at a garden gate.",
        "stop_text": "She has turned to face the gate, one hand on the latch.",
        "motion_prompt": "She lifts the latch.",
        "references": "girl_front",
    }
    row.update(overrides)
    return row


def add_reference(project_dir: Path, stem: str) -> None:
    write_image(project_dir / "references" / f"{stem}.png", size=(256, 192))


def test_complete_project_has_no_errors(tmp_path):
    dest = make_project(tmp_path, [good_row()])
    add_reference(dest, "girl_front")
    issues = planfile.check(dest)
    assert [i for i in issues if i.level == "error"] == []
    stats = planfile.summarise(issues)
    assert stats["shots"] == 1
    assert stats["seconds"] == 7.0
    assert stats["references"] == 1


def test_missing_reference_is_an_error(tmp_path):
    dest = make_project(tmp_path, [good_row(references="girl_front;gate_plate")])
    add_reference(dest, "girl_front")
    errors = [i for i in planfile.check(dest) if i.level == "error"]
    assert len(errors) == 1
    assert "gate_plate" in errors[0].message


def test_duplicate_shot_id_is_an_error(tmp_path):
    dest = make_project(tmp_path, [good_row(), good_row(start_text="Another frame entirely, same shot number.")])
    add_reference(dest, "girl_front")
    errors = [i for i in planfile.check(dest) if i.level == "error"]
    assert any("duplicate shot id" in i.message for i in errors)


def test_empty_stop_text_is_an_error(tmp_path):
    dest = make_project(tmp_path, [good_row(stop_text="")])
    add_reference(dest, "girl_front")
    errors = [i for i in planfile.check(dest) if i.level == "error"]
    assert any("stop text is empty" in i.message for i in errors)


def test_quoted_dialogue_is_an_error(tmp_path):
    dest = make_project(tmp_path, [good_row(start_text='She says "good morning" to the gate.')])
    add_reference(dest, "girl_front")
    errors = [i for i in planfile.check(dest) if i.level == "error"]
    assert any("contains quotes" in i.message for i in errors)


def test_bad_duration_is_an_error(tmp_path):
    dest = make_project(tmp_path, [good_row(duration_s="soon")])
    add_reference(dest, "girl_front")
    errors = [i for i in planfile.check(dest) if i.level == "error"]
    assert any("not a number" in i.message for i in errors)


def test_long_duration_warns_but_passes(tmp_path):
    dest = make_project(tmp_path, [good_row(duration_s="14")])
    add_reference(dest, "girl_front")
    issues = planfile.check(dest)
    assert [i for i in issues if i.level == "error"] == []
    assert any("long" in i.message for i in issues if i.level == "warn")


def test_off_grid_project_size_is_an_error(tmp_path):
    dest = make_project(tmp_path, [good_row()], project={"name": "Film", "width": 1000, "height": 768})
    add_reference(dest, "girl_front")
    errors = [i for i in planfile.check(dest) if i.level == "error"]
    assert any("64 grid" in i.message for i in errors)


def test_missing_file_is_reported_without_crashing(tmp_path):
    dest = tmp_path / "empty"
    dest.mkdir()
    issues = planfile.check(dest)
    assert any(i.level == "error" and "missing" in i.message for i in issues)


def test_negation_warns(tmp_path):
    dest = make_project(tmp_path, [good_row(start_text="A quiet yard, no hens anywhere.")])
    add_reference(dest, "girl_front")
    issues = planfile.check(dest)
    assert [i for i in issues if i.level == "error"] == []
    assert any("negation" in i.message for i in issues if i.level == "warn")
