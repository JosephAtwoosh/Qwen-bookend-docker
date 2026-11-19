"""Reference selection and the manifest: what goes into a generation, and drift detection."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bookends import refs
from bookends.config import Config
from bookends.shot import load_shot
from tests.conftest import write_image


def make_config(tmp_path: Path, **kw) -> Config:
    base = dict(
        shots_dir=tmp_path / "shots",
        reference_dir=tmp_path / "reference",
        output_dir=tmp_path / "output",
        models_dir=tmp_path / "models",
        width=1280,
        height=768,
    )
    base.update(kw)
    return Config(**base)


def test_without_selection_globals_are_capped_and_reported(tmp_path, shot_factory):
    cfg = make_config(tmp_path)
    for i in range(5):
        write_image(cfg.reference_dir / f"g{i}.png")
    shot = load_shot(shot_factory("shot_001", refs=0, plates=0))

    selection = refs.select(cfg, shot)
    assert [p.name for p in selection.images] == ["g0.png", "g1.png", "g2.png"]
    assert any("no selection" in n for n in selection.notes)


def test_use_txt_sets_order_and_excludes(tmp_path, shot_factory):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "char_front.png")
    write_image(cfg.reference_dir / "plate_garden.png")
    shot = load_shot(shot_factory("shot_001", refs=1, plates=1))
    (shot.root / "use.txt").write_text(
        "plate_garden.png\nchar_front.png\n-ref_char_0.png\n", encoding="utf-8"
    )

    selection = refs.select(cfg, shot)
    assert [p.name for p in selection.images] == ["plate_garden.png", "char_front.png", "plate_set_0.png"]


def test_default_txt_applies_project_wide(tmp_path, shot_factory):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    write_image(cfg.reference_dir / "b.png")
    (cfg.reference_dir / "default.txt").write_text("# the cast\nb.png\n", encoding="utf-8")
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))

    selection = refs.select(cfg, shot)
    assert [p.name for p in selection.images] == ["b.png", "ref_char_0.png"]


def test_shot_use_txt_beats_default_txt(tmp_path, shot_factory):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    write_image(cfg.reference_dir / "b.png")
    (cfg.reference_dir / "default.txt").write_text("a.png\n", encoding="utf-8")
    shot = load_shot(shot_factory("shot_001", refs=0, plates=0))
    (shot.root / "use.txt").write_text("b.png\n", encoding="utf-8")

    assert [p.name for p in refs.select(cfg, shot).images] == ["b.png"]


def test_unknown_name_fails_before_any_gpu_work(tmp_path, shot_factory):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    (shot.root / "use.txt").write_text("typo.png\n", encoding="utf-8")

    with pytest.raises(refs.SelectionError) as exc:
        refs.select(cfg, shot)
    assert "typo.png" in str(exc.value)
    assert "a.png" in str(exc.value)  # the error lists what was available


def test_shot_local_file_shadows_shared_one(tmp_path, shot_factory):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "char.png")
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    write_image(shot.root / "char.png")
    (shot.root / "use.txt").write_text("char.png\n", encoding="utf-8")

    selection = refs.select(cfg, shot)
    assert selection.images[0] == shot.root / "char.png"


def test_manifest_roundtrip_and_drift(tmp_path):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    (cfg.reference_dir / "refs.json").write_text('{"a.png": "the character"}', encoding="utf-8")

    path, count = refs.write_manifest(cfg)
    assert path.is_file() and count == 2  # a.png + refs.json

    problems, checked = refs.verify_manifest(cfg)
    assert problems == [] and checked == 2

    # A changed label rewrites every prompt in the project, so it must be visible.
    (cfg.reference_dir / "refs.json").write_text('{"a.png": "someone else"}', encoding="utf-8")
    problems, _ = refs.verify_manifest(cfg)
    assert problems == ["changed: refs.json"]


def test_manifest_reports_unlisted_and_missing(tmp_path):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    refs.write_manifest(cfg)
    write_image(cfg.reference_dir / "b.png")  # added after the manifest
    problems, _ = refs.verify_manifest(cfg)
    assert any(p.startswith("unlisted: b.png") for p in problems)

    # 'missing' is checked without touching the filesystem: a manifest entry whose
    # file is not there is exactly what a truncated transfer looks like.
    digest = hashlib.sha256(b"x").hexdigest()
    (cfg.reference_dir / refs.MANIFEST_NAME).write_text(f"{digest}  ghost.png\n", encoding="utf-8")
    problems, _ = refs.verify_manifest(cfg)
    assert "missing: ghost.png" in problems


def test_absent_manifest_is_not_silent(tmp_path):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    problems, checked = refs.verify_manifest(cfg)
    assert checked == 0
    assert any("no manifest.sha256" in p for p in problems)
