"""Prompt composition: the anti-boilerplate rules are enforced, not suggested."""
from __future__ import annotations

from pathlib import Path

from bookends.prompt import (
    PLATE_LABEL,
    NO_TEXT_CLAUSE,
    STYLES,
    compose,
    default_label,
    describe_images,
    style_suffix,
)


def test_describe_numbers_every_image():
    images = [Path("ref_front.png"), Path("ref_side.png"), Path("plate_set.png")]
    text = describe_images(images)
    assert text.startswith("Image 1 shows the front reference.")
    assert "Image 2 shows the side reference." in text
    assert f"Image 3 shows {PLATE_LABEL}." in text


def test_labels_override_filenames():
    images = [Path("a.png"), Path("b.png")]
    text = describe_images(images, {"a.png": "the character, front view", "b.png": "the garden"})
    assert "Image 1 shows the character, front view." in text
    assert "Image 2 shows the garden." in text


def test_default_label_strips_prefix():
    assert default_label(Path("ref_char_front_view.png")) == "the char front view reference"
    assert default_label(Path("ref_3.png")) == "reference image 1"


def test_compose_always_appends_the_no_text_clause():
    prompt, notes = compose("She turns away.", [Path("ref_a.png")])
    assert prompt.endswith(NO_TEXT_CLAUSE)
    assert prompt.startswith("Image 1 shows the a reference.")
    assert notes == []


def test_over_budget_body_is_reported_not_truncated():
    body = "word " * 140
    prompt, notes = compose(body.strip(), [], max_words=120)
    assert any("over the 120-word budget" in n for n in notes)
    assert body.strip() in prompt  # the text is kept; the operator decides


def test_quoted_dialogue_is_flagged():
    prompt, notes = compose('He says "good morning" quietly.', [])
    assert any("quotation marks" in n for n in notes)


def test_negation_is_flagged():
    _, notes = compose("The yard is empty, no hens at all.", [])
    assert any("negation" in n for n in notes)


def test_no_images_is_flagged():
    _, notes = compose("A wide shot of the field.", [])
    assert any("text-only" in n for n in notes)


def test_builtin_style_suffix():
    suffix = style_suffix("clay")
    assert suffix.startswith(STYLES["clay"])
    assert style_suffix(None).startswith("Keep the subject")


def test_free_text_style_is_accepted():
    assert style_suffix("grainy 1970s Super 8").startswith("grainy 1970s Super 8.")
