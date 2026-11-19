"""Review metrics: the two failure modes that cost renders in take 2."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from bookends import review
from bookends.config import Config
from bookends.shot import load_shot
from tests.conftest import write_image


def make_config(tmp_path: Path) -> Config:
    return Config(
        shots_dir=tmp_path / "shots",
        reference_dir=tmp_path / "reference",
        output_dir=tmp_path / "output",
        models_dir=tmp_path / "models",
        width=640,
        height=384,
    )


def _pair(shot, start_img: Image.Image, stop_img: Image.Image):
    start_img.save(shot.root / "start.jpg", quality=95)
    stop_img.save(shot.root / "stop.jpg", quality=95)
    return shot


def test_ssim_identical_is_one():
    a = np.asarray(Image.new("L", (64, 64), 128), dtype=np.float64)
    assert review.ssim(a, a) > 0.999


def test_ssim_drops_with_difference():
    a = np.zeros((64, 64)) + 40
    b = np.zeros((64, 64)) + 200
    assert review.ssim(a, b) < 0.6


def test_identical_pair_is_stalled(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    same = Image.open(write_image(tmp_path / "tmp.png", size=(640, 384))).convert("RGB")
    _pair(shot, same, same)

    result = review.measure(shot)
    assert result.verdict == "stalled"
    assert result.ssim and result.ssim > 0.97
    assert any("nearly the same frame" in r for r in result.reasons)

    summary = review.build(cfg, [shot])
    assert summary["verdicts"]["stalled"] == 1
    assert (tmp_path / "output" / "review" / "index.html").is_file()


def test_opposite_images_are_drift(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))

    warm = Image.new("RGB", (640, 384), (250, 120, 40))
    draw = ImageDraw.Draw(warm)
    for x in range(0, 640, 40):
        draw.rectangle([x, 0, x + 20, 384], fill=(255, 240, 60))
    cold = Image.new("RGB", (640, 384), (20, 40, 120))
    draw = ImageDraw.Draw(cold)
    for y in range(0, 384, 40):
        draw.rectangle([0, y, 640, y + 20], fill=(10, 10, 30))
    _pair(shot, warm, cold)

    result = review.measure(shot)
    assert result.verdict == "drift"
    assert result.ssim is not None and result.ssim < 0.55


def test_small_move_passes(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    base = write_image(tmp_path / "a.png", size=(640, 384), sun=(200, 150))
    moved = write_image(tmp_path / "b.png", size=(640, 384), sun=(260, 150))
    _pair(shot, Image.open(base).convert("RGB"), Image.open(moved).convert("RGB"))

    result = review.measure(shot)
    assert result.verdict == "pass", result.reasons


def test_missing_frames_are_reported(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    write_image(shot.root / "start.jpg", size=(640, 384))

    result = review.measure(shot)
    assert result.verdict == "missing"
    assert any("stop.jpg" in r for r in result.reasons)


def test_build_writes_csv_json_and_thumbs(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    base = Image.open(write_image(tmp_path / "a.png", size=(640, 384))).convert("RGB")
    moved = Image.open(write_image(tmp_path / "b.png", size=(640, 384), sun=(240, 160))).convert("RGB")
    _pair(shot, base, moved)

    review.build(cfg, [shot], thumb_width=200)

    review_dir = tmp_path / "output" / "review"
    with open(review_dir / "metrics.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["shot"] == "shot_001"
    assert rows[0]["ssim"]

    payload = json.loads((review_dir / "review.json").read_text(encoding="utf-8"))
    assert payload["shots"] == 1

    html = (review_dir / "index.html").read_text(encoding="utf-8")
    assert "shot_001" in html
    assert "Bookend review" in html

    thumbs = list((review_dir / "thumbs").glob("*.jpg"))
    assert len(thumbs) == 2
    with Image.open(thumbs[0]) as im:
        assert im.width == 200


def test_build_does_not_duplicate_reasons(shot_factory, tmp_path):
    """Regression: build() used to judge an already-judged pair, doubling reasons."""
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    same = Image.open(write_image(tmp_path / "same.png", size=(640, 384))).convert("RGB")
    _pair(shot, same, same)

    review.build(cfg, [shot])
    again = review.measure(shot)
    assert again.reasons
    assert len(again.reasons) == len(set(again.reasons))


def test_sidecar_seeds_are_surfaced(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    base = Image.open(write_image(tmp_path / "a.png", size=(640, 384))).convert("RGB")
    _pair(shot, base, base)
    for tag, seed in (("start", 11), ("stop", 22)):
        (shot.root / f"{tag}.json").write_text(json.dumps({"seed": seed, "seconds": 4.5}), encoding="utf-8")

    result = review.measure(shot)
    assert (result.seed_start, result.seed_stop) == (11, 22)
    assert result.seconds == 4.5
