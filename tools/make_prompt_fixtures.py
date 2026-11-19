#!/usr/bin/env python3
"""Generate the prompt-conformance fixtures the PHP studio is tested against.

The studio reimplements prompt composition in PHP so it can preview a prompt
without a round trip to Python. Two implementations of one rule set drift, so the
Python one -- which is the one `bookends batch` actually uses -- emits golden
output here, and `studio/tests/run.php` fails if PHP disagrees by one character.

    python tools/make_prompt_fixtures.py

Writes studio/tests/fixtures/prompts.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bookends.prompt import PLATE_LABEL, START_FRAME_LABEL, STYLES, compose, default_label, describe_images  # noqa: E402

CASES = [
    {
        "name": "two refs, no style",
        "body": "She stands at the gate in the morning sun.",
        "images": ["char_toby_front.png", "set_garden_plate.png"],
        "labels": {"char_toby_front.png": "the character Toby, front view",
                   "set_garden_plate.png": "the garden with the coop, nobody present"},
        "style": None,
    },
    {
        "name": "built-in style",
        "body": "She has turned to face the coop, looking down.",
        "images": ["a.png"],
        "labels": {"a.png": "a small brown terrier with a red collar, front view"},
        "style": "clay",
    },
    {
        "name": "free-text style",
        "body": "He raises the lantern.",
        "images": ["a.png", "b.png"],
        "labels": {"a.png": "the character Toby", "b.png": "the lantern prop"},
        "style": "grainy 1970s Super 8",
    },
    {
        "name": "no images",
        "body": "A wide shot of the empty field at dusk.",
        "images": [],
        "labels": {},
        "style": None,
    },
    {
        "name": "over budget",
        "body": " ".join(["word"] * 140),
        "images": ["a.png"],
        "labels": {"a.png": "the subject"},
        "style": "pixar",
    },
    {
        "name": "quotes and negation",
        "body": 'He says "good morning" quietly, and no hens are visible.',
        "images": ["a.png"],
        "labels": {"a.png": "the subject"},
        "style": None,
    },
    {
        "name": "stop frame appended",
        "body": "She has turned away.",
        "images": ["char_toby_front.png", "start.jpg"],
        "labels": {"char_toby_front.png": "the character Toby, front view", "start.jpg": START_FRAME_LABEL},
        "style": "noir",
    },
]


def main() -> int:
    out = {"generated_by": "tools/make_prompt_fixtures.py", "cases": []}
    for case in CASES:
        images = [Path(p) for p in case["images"]]
        prompt, notes = compose(
            case["body"],
            images,
            labels=case["labels"],
            style=case["style"],
            max_words=120,
        )
        out["cases"].append(
            {
                "name": case["name"],
                "body": case["body"],
                "labels": [case["labels"][n] for n in case["images"]],
                "style": case["style"],
                "expected_prompt": prompt,
                "expected_notes": notes,
            }
        )

    # A couple of pure-function cases as well.
    out["default_labels"] = [
        {"file": "ref_char_front_view.png", "expected": default_label(Path("ref_char_front_view.png"))},
        {"file": "ref_3.png", "expected": default_label(Path("ref_3.png"))},
        {"file": "plate_set.png", "expected": default_label(Path("plate_set.png"), is_plate=True)},
    ]
    out["describe"] = {
        "labels": ["the character Toby, front view", "the garden, nobody present"],
        "expected": describe_images(
            [Path("a.png"), Path("b.png")],
            {"a.png": "the character Toby, front view", "b.png": "the garden, nobody present"},
        ),
    }
    out["styles"] = dict(STYLES)
    out["plate_label"] = PLATE_LABEL
    out["start_frame_label"] = START_FRAME_LABEL

    dest = ROOT / "studio" / "tests" / "fixtures" / "prompts.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest} ({len(out['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
