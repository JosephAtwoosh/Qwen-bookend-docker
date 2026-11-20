"""Reference capping: bound the token cost of the images we feed in.

Measured context (RTX 5000 Ada, 1280x768 output, 28 steps): a start with two
references took 159 s, a stop with three took 229 s -- the extra cost is the extra
image's latent tokens, not the extra text.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from bookends.pipe import prepare_images


def make_image(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, (120, 90, 60)).save(path)
    return path


def test_large_images_are_capped_on_the_long_edge(tmp_path):
    plate = make_image(tmp_path / "plate.png", (1672, 941))
    images, notes = prepare_images([plate], max_edge=768)
    assert images[0].size == (768, 432)
    assert notes and "1672x941 -> 768x432" in notes[0]


def test_small_images_are_left_alone(tmp_path):
    hen = make_image(tmp_path / "hen.png", (508, 713))
    images, notes = prepare_images([hen], max_edge=768)
    assert images[0].size == (508, 713)
    assert notes == []


def test_cap_can_be_disabled(tmp_path):
    plate = make_image(tmp_path / "plate.png", (1672, 941))
    images, notes = prepare_images([plate], max_edge=0)
    assert images[0].size == (1672, 941)
    assert notes == []


def test_portrait_and_landscape_both_cap_on_the_long_edge(tmp_path):
    tall = make_image(tmp_path / "tall.png", (600, 1800))
    wide = make_image(tmp_path / "wide.png", (1800, 600))
    images, _ = prepare_images([tall, wide], max_edge=900)
    assert images[0].size == (300, 900)
    assert images[1].size == (900, 300)


def test_config_defaults_and_validation(tmp_path):
    from bookends.config import Config, ConfigError

    assert Config().ref_max_edge == 768
    cfg = Config(ref_max_edge=128)
    try:
        cfg.validate()
    except ConfigError as exc:
        assert "ref_max_edge" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a 128 px cap should be rejected")
