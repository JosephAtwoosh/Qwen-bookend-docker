"""Put the character references in the film's world.

The turnarounds are studio shots: a chicken on a pale sweep. Handing one to the edit
model and nothing else makes it reproduce the sweep — that is what happened to shots
5, 6, 7. Pairing every reference with the plate fixes the ones with a spare image slot,
but a stop has only two slots and sometimes both are characters.

So the fix belongs in the reference itself: matte the character off the sweep and
composite it onto the winter plate. Then any reference carries the yard, and a shot no
longer needs a third slot for the set.

Matting is a border flood fill on colour distance, because the sweeps are near-uniform
and the subject is not. No segmentation model, no downloads.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
REFS = ROOT / "plan" / "refs"
PLATE = REFS / "sets" / "cherry_tree_farm_winter.png"
OUT = ROOT / "plan" / "refs-farm"

CHARS = [
    "frederick_front",
    "frederick_side",
    "frederick_three_quarter",
    "mildred_front",
    "mildred_side",
    "mildred_three_quarter",
    "hilda_front",
    "hilda_side",
    "hilda_three_quarter",
    "gerry_front",
    "gerry_side",
    "gerry_three_quarter",
    "henry_front",
    "henry_side",
    "henry_three_quarter",
    "hubbard_front",
    "petra_front",
    "oliver_front",
]

TOLERANCE = 26.0      # colour distance that still counts as background
FEATHER = 2.2         # blur radius on the matte edge, in px
HEIGHT_FRAC = 0.56    # character height as a share of the plate height
CENTRE_X = 0.40       # horizontal placement on the plate
GROUND_Y = 0.87       # where the feet land


def background_mask(rgb: np.ndarray, tolerance: float = TOLERANCE) -> np.ndarray:
    """Flood fill inwards from the border, stopping where the colour changes."""
    h, w, _ = rgb.shape
    border = np.concatenate([rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]])
    ref = np.median(border, axis=0)

    close = np.abs(rgb - ref).max(axis=2) <= tolerance
    bg = np.zeros((h, w), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    for x in range(w):
        queue.append((0, x))
        queue.append((h - 1, x))
    for y in range(h):
        queue.append((y, 0))
        queue.append((y, w - 1))

    while queue:
        y, x = queue.popleft()
        if bg[y, x] or not close[y, x]:
            continue
        bg[y, x] = True
        if y > 0:
            queue.append((y - 1, x))
        if y < h - 1:
            queue.append((y + 1, x))
        if x > 0:
            queue.append((y, x - 1))
        if x < w - 1:
            queue.append((y, x + 1))
    return bg


def cutout(path: Path) -> Image.Image:
    """The subject with its sweep removed, as RGBA."""
    im = Image.open(path).convert("RGB")
    if max(im.size) > 900:
        scale = 900 / max(im.size)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    rgb = np.asarray(im, dtype=np.float32)
    bg = background_mask(rgb)

    alpha = np.where(bg, 0, 255).astype(np.uint8)
    alpha_im = Image.fromarray(alpha, mode="L").filter(ImageFilter.GaussianBlur(FEATHER))
    # Shrink the matte slightly so a rim of backdrop cannot ride along with the subject.
    alpha_im = alpha_im.filter(ImageFilter.MinFilter(3))
    alpha_im = alpha_im.filter(ImageFilter.GaussianBlur(0.8))
    out = im.convert("RGBA")
    out.putalpha(alpha_im)
    return out


def place(subject: Image.Image, plate_size: tuple[int, int]) -> Image.Image:
    plate = Image.open(PLATE).convert("RGB")
    pw, ph = plate.size

    target_h = int(ph * HEIGHT_FRAC)
    scale = target_h / subject.height
    subj = subject.resize((max(1, int(subject.width * scale)), target_h), Image.LANCZOS)

    cx = int(pw * CENTRE_X)
    ground = int(ph * GROUND_Y)
    x0 = cx - subj.width // 2
    y0 = ground - subj.height
    if y0 < 0:
        y0 = 0

    # Contact shadow: an ellipse on the ground, blurred, so the bird sits on the mud
    # rather than floating over it.
    shadow = Image.new("L", plate.size, 0)
    draw = ImageDraw.Draw(shadow)
    sw = int(subj.width * 0.78)
    sh = max(10, int(target_h * 0.06))
    draw.ellipse([cx - sw // 2, ground - sh // 2, cx + sw // 2, ground + sh // 2], fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(3, target_h * 0.03)))
    plate = Image.composite(Image.new("RGB", plate.size, (28, 22, 18)), plate, shadow)

    plate.paste(subj, (x0, y0), subj)
    return plate


def main() -> int:
    if not PLATE.is_file():
        raise SystemExit(f"no plate at {PLATE}")
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for name in CHARS:
        src = REFS / f"{name}.png"
        if not src.is_file():
            src = REFS / "sets" / f"{name}.png"
        if not src.is_file():
            print(f"  skip {name}: no such reference")
            continue
        subject = cutout(src)
        composed = place(subject, Image.open(PLATE).size)
        dest = OUT / f"{name}.png"
        composed.save(dest)
        kept = (np.asarray(subject.split()[-1]) > 8).mean()
        print(f"  {name:26} subject covers {kept:.0%} of the frame -> {dest.name}")
        made += 1
    print(f"\n{made} references composited into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
