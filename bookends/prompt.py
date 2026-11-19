"""Prompt composition.

The rule this enforces is the one from `PRODUCTION.md`: a prompt describes one
shot, briefly. So a shot's `start.txt` / `stop.txt` say *only* what changed from
the references, and the surrounding boilerplate -- what each reference image is,
which look the project uses, and that no lettering may appear -- is assembled
here, once, instead of being pasted into every text file by hand.

Take 2 averaged 505 words per prompt with 15.5 negations, and the model answered
by blending species and lettering dialogue into frame. The cap below is the
correction: `--max-prompt-words` (default 120) is reported against the shot body,
and `prep` surfaces every overrun.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_STYLE_SUFFIX, NO_TEXT_CLAUSE

# Reusable looks. Deliberately phrased as "keep X" rather than "no Y": an
# absence is better explained than forbidden (see PRODUCTION.md rule 3).
STYLES: dict[str, str] = {
    "pixar": (
        "3D Pixar-style animated film look, expressive subsurface-lit materials, soft cinematic "
        "depth of field, cinematic key light"
    ),
    "clay": (
        "stop-motion claymation look, handcrafted plasticine texture with faint fingerprints, "
        "miniature set lighting, shallow depth of field"
    ),
    "cartoon": (
        "2D modern cel-shaded cartoon illustration, bold clean linework, flat shading with a rim light"
    ),
    "anime": (
        "theatrical anime key visual, crisp line art, cel-shaded lighting, rich colour grading"
    ),
    "noir": (
        "1940s black-and-white film noir still, high-contrast chiaroscuro lighting, 35mm film grain"
    ),
    "realistic": (
        "photorealistic live-action film still, 35mm anamorphic lens, natural skin and material "
        "texture, cinematic lighting"
    ),
}

PLATE_LABEL = "the empty background plate for this set, with nobody in frame"
START_FRAME_LABEL = "frame 0 of this shot, already approved and rendered"


def load_labels(folder: Path) -> dict[str, str]:
    """Read `refs.json` (filename -> noun phrase) if the project supplies one."""
    folder = Path(folder)
    path = folder / "refs.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object mapping filename -> description")
    return {str(k): str(v) for k, v in data.items()}


def default_label(path: Path, *, is_plate: bool = False, index: int = 1) -> str:
    """A readable noun phrase from a filename, when no refs.json says otherwise."""
    if is_plate:
        return PLATE_LABEL
    stem = Path(path).stem
    for prefix in ("ref_", "plate_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    words = stem.replace("_", " ").replace("-", " ").strip()
    if not words or words.isdigit():
        return f"reference image {index}"
    return f"the {words} reference"


def describe_images(images: list[Path], labels: dict[str, str] | None = None) -> str:
    """Build the `Image 1 shows ...` preamble that the model card's examples rely on."""
    labels = labels or {}
    lines: list[str] = []
    for i, img in enumerate(images, start=1):
        img = Path(img)
        label = labels.get(img.name) or labels.get(str(img))
        if not label:
            label = default_label(img, is_plate=img.name.startswith("plate_"), index=i)
        lines.append(f"Image {i} shows {label}.")
    return " ".join(lines)


def style_suffix(style: str | None) -> str:
    if not style:
        return DEFAULT_STYLE_SUFFIX
    key = style.strip().lower()
    if key in STYLES:
        return STYLES[key] + ". " + DEFAULT_STYLE_SUFFIX
    # A free-text style is allowed: the bank is a convenience, not a cage.
    return style.strip().rstrip(".") + ". " + DEFAULT_STYLE_SUFFIX


def compose(
    body: str,
    images: list[Path],
    *,
    labels: dict[str, str] | None = None,
    style: str | None = None,
    max_words: int = 120,
) -> tuple[str, list[str]]:
    """Assemble the final prompt and return it with any warnings."""
    body = body.strip()
    if not body:
        raise ValueError("shot text is empty")

    notes: list[str] = []
    words = len(body.split())
    if words > max_words:
        notes.append(
            f"shot text is {words} words, over the {max_words}-word budget; the model spreads "
            f"attention across boilerplate when a prompt is long"
        )
    if '"' in body or "\u201c" in body or "\u201d" in body:
        notes.append(
            "shot text contains quotation marks; if this is dialogue it will be lettered into the "
            "frame. Describe what is seen and heard, not what is said."
        )
    if "no " in body.lower():
        notes.append(
            "shot text contains a negation; prefer explaining the absence ('the white hen is "
            "inside the coop') over forbidding it ('no hens')"
        )

    preamble = describe_images(images, labels)
    parts = [p for p in (preamble, body, style_suffix(style), NO_TEXT_CLAUSE) if p]
    prompt = " ".join(parts)
    if len(images) == 0:
        notes.append("no reference images: this is a text-only generation")
    return prompt, notes
