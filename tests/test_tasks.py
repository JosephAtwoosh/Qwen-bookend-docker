"""Task resolution: deterministic seeds, stable image order, cheap re-rolls."""
from __future__ import annotations

from pathlib import Path

from bookends.config import Config
from bookends.shot import load_shot
from bookends.tasks import derive_seed, global_references, pending, render_order, resolve
from tests.conftest import write_image


def make_config(tmp_path: Path, **kw) -> Config:
    base = dict(
        shots_dir=tmp_path / "shots",
        reference_dir=tmp_path / "reference",
        output_dir=tmp_path / "output",
        models_dir=tmp_path / "models",
    )
    base.update(kw)
    return Config(**base)


def test_seed_is_deterministic_and_distinct():
    a = derive_seed("shot_001", "start", 42)
    b = derive_seed("shot_001", "start", 42)
    c = derive_seed("shot_001", "stop", 42)
    d = derive_seed("shot_002", "start", 42)
    assert a == b
    assert len({a, c, d}) == 3
    assert 0 <= a < 2**31


def test_seed_changes_with_base():
    assert derive_seed("shot_001", "start", 42) != derive_seed("shot_001", "start", 43)


def test_image_order_is_global_then_shot_then_plate(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    for i in range(2):
        write_image(cfg.reference_dir / f"char_{i}.png")
    shot = load_shot(shot_factory("shot_001", refs=2, plates=1))

    tasks, _ = resolve(cfg, [shot])
    start = next(t for t in tasks if t.tag == "start")
    names = [p.name for p in start.images]
    assert names == ["char_0.png", "char_1.png", "ref_char_0.png", "ref_char_1.png", "plate_set_0.png"]


def test_sequential_stop_consumes_start(shot_factory, tmp_path):
    cfg = make_config(tmp_path, pair_mode="sequential")
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))

    tasks, notes = resolve(cfg, [shot])
    stop = next(t for t in tasks if t.tag == "stop")
    assert stop.images[-1].name != "start.jpg"
    assert any("start.jpg is absent" in n for n in notes)

    write_image(shot.root / "start.jpg", size=(1280, 768))
    tasks, notes = resolve(cfg, [load_shot(shot.root)])
    stop = next(t for t in tasks if t.tag == "stop")
    assert stop.images[-1] == shot.root / "start.jpg"
    assert not any("start.jpg is absent" in n for n in notes)
    assert "shows frame 0 of this shot" in stop.prompt


def test_independent_stop_ignores_start(shot_factory, tmp_path):
    cfg = make_config(tmp_path, pair_mode="independent")
    shot = load_shot(shot_factory("shot_001", refs=1, plates=1))
    write_image(shot.root / "start.jpg", size=(1280, 768))

    tasks, _ = resolve(cfg, [shot])
    stop = next(t for t in tasks if t.tag == "stop")
    assert shot.root / "start.jpg" not in stop.images


def test_pending_skips_existing_and_force_overrides(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    tasks, _ = resolve(cfg, [shot])
    assert len(pending(tasks)) == 2

    write_image(shot.root / "start.jpg", size=(64, 64))
    todo = pending(tasks)
    assert [t.tag for t in todo] == ["stop"]
    assert len(pending(tasks, force=True)) == 2


def test_render_order_puts_starts_first(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shots = [load_shot(shot_factory(f"shot_{i:03d}")) for i in (1, 2)]
    tasks, _ = resolve(cfg, shots)
    ordered = render_order(tasks, "sequential")
    tags = [t.tag for t in ordered]
    assert tags[: len(shots)] == ["start"] * len(shots)
    assert set(tags[len(shots) :]) == {"stop"}


def test_no_references_is_reported(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=0, plates=0))
    tasks, notes = resolve(cfg, [shot])
    assert any("no global references" in n for n in notes)
    assert any("text-only" in n for t in tasks for n in t.notes)


def test_global_references_filters_non_images(tmp_path):
    cfg = make_config(tmp_path)
    write_image(cfg.reference_dir / "a.png")
    write_image(cfg.reference_dir / "b.jpg")
    (cfg.reference_dir / "refs.json").write_text("{}", encoding="utf-8")
    (cfg.reference_dir / "notes.txt").write_text("hello", encoding="utf-8")
    assert [p.name for p in global_references(cfg)] == ["a.png", "b.jpg"]
