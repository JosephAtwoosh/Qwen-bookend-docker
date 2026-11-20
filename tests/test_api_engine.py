"""The hosted engine, tested without a network.

Measured context (2026-11-20): the same shot and seed produced a start in 13.2 s and a
stop in 18.2 s hosted, against 157 s and 229 s on a rented RTX 5000 Ada. So the hosted
path is what the film will actually use, and it has to be tested offline.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from bookends.config import Config, ConfigError
from bookends.pipe import ApiEngine, data_uri, find_image_url, prepare_images


def make_ref(path: Path, size=(508, 713), colour=(200, 120, 40)) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


@pytest.fixture(autouse=True)
def _hosted_key(monkeypatch):
    """ApiEngine.start() refuses to run without a key, by design."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-sp-test-key")


class FakePost:
    """Records requests and answers like Token Plan."""

    def __init__(self, image_bytes: bytes):
        self.calls = []
        self._image = image_bytes

    def __call__(self, url, key, body, timeout):
        self.calls.append({"url": url, "key": key, "body": body, "timeout": timeout})
        return {"output": {"choices": [{"message": {"content": [{"image": "https://example.test/a.png"}]}}]},
                "usage": {"output_image_count": 1}}


def png_bytes(size=(1280, 768)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def task(images, prompt="a hen", seed=1234, shot="shot_001", tag="start"):
    from bookends.tasks import Task

    return Task(shot=shot, tag=tag, prompt=prompt, seed=seed, images=images, out=Path("out.jpg"))


def test_request_body_matches_the_documented_contract(tmp_path):
    ref = make_ref(tmp_path / "ref.png")
    post = FakePost(png_bytes())
    cfg = Config(engine="tokenplan", width=1280, height=768)
    engine = ApiEngine(cfg, poster=post, fetcher=lambda url: png_bytes())
    engine.start()
    engine.generate(task([ref], seed=999))

    body = post.calls[0]["body"]
    assert body["model"] == "qwen-image-3.0-pro"
    assert body["parameters"]["size"] == "1280*768"      # letter-* separator
    assert body["parameters"]["seed"] == 999
    assert body["parameters"]["prompt_extend"] is False  # keep our wording verbatim
    content = body["input"]["messages"][0]["content"]
    assert content[0]["image"].startswith("data:image/jpeg;base64,")
    assert content[-1] == {"text": "a hen"}


def test_image_order_is_preserved_and_start_stays_last(tmp_path):
    """The prompt says "Image 1 / Image 2 / Image 3", so order is the contract."""
    a = make_ref(tmp_path / "a.png", colour=(255, 0, 0))
    b = make_ref(tmp_path / "b.png", colour=(0, 255, 0))
    c = make_ref(tmp_path / "c.png", colour=(0, 0, 255))
    post = FakePost(png_bytes())
    engine = ApiEngine(Config(engine="tokenplan"), poster=post, fetcher=lambda url: png_bytes())
    engine.start()
    engine.generate(task([a, b, c], tag="stop"))

    content = post.calls[0]["body"]["input"]["messages"][0]["content"]
    payload = [part["image"] for part in content if "image" in part]
    assert len(payload) == 3
    # Decode and check the dominant channel of each, in order.
    for index, expected in enumerate([0, 1, 2]):
        raw = payload[index].split(",", 1)[1]
        import base64

        im = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
        channel = im.getpixel((im.width // 2, im.height // 2))[expected]
        assert channel > 200, f"image {index + 1} is not the reference passed at {index}"


def test_references_are_capped_before_upload(tmp_path):
    plate = make_ref(tmp_path / "plate.png", size=(1672, 941))
    post = FakePost(png_bytes())
    engine = ApiEngine(Config(engine="tokenplan", ref_max_edge=768), poster=post, fetcher=lambda url: png_bytes())
    engine.start()
    engine.generate(task([plate]))

    import base64

    payload = post.calls[0]["body"]["input"]["messages"][0]["content"][0]["image"]
    im = Image.open(io.BytesIO(base64.b64decode(payload.split(",", 1)[1])))
    assert max(im.size) == 768
    assert any("reference downscaled" in note for note in engine.notes)


def test_no_key_is_a_clear_error_not_a_stack_trace(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    engine = ApiEngine(Config(engine="tokenplan", api_key="", api_key_file=""), poster=FakePost(png_bytes()))
    with pytest.raises(RuntimeError) as exc:
        engine.start()
    assert "API key" in str(exc.value)


def test_key_can_come_from_a_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-sp-test-key\n", encoding="utf-8")
    engine = ApiEngine(Config(engine="tokenplan", api_key_file=str(key_file)), poster=FakePost(png_bytes()))
    engine.start()
    assert engine._key == "sk-sp-test-key"


def test_missing_image_in_response_is_an_error(tmp_path):
    ref = make_ref(tmp_path / "ref.png")
    engine = ApiEngine(Config(engine="tokenplan"), poster=lambda *a: {"output": {}}, fetcher=lambda url: b"")
    engine.start()
    with pytest.raises(RuntimeError) as exc:
        engine.generate(task([ref]))
    assert "no image" in str(exc.value)


def test_find_image_url_accepts_both_shapes():
    token_plan = {"output": {"choices": [{"message": {"content": [{"text": "x"}, {"image": "u1"}]}}]}}
    openai_ish = {"data": [{"url": "u2"}]}
    assert find_image_url(token_plan) == "u1"
    assert find_image_url(openai_ish) == "u2"
    assert find_image_url({}) is None


def test_smaller_output_than_requested_is_recorded(tmp_path):
    ref = make_ref(tmp_path / "ref.png")
    engine = ApiEngine(
        Config(engine="tokenplan", width=1280, height=768),
        poster=FakePost(png_bytes()),
        fetcher=lambda url: png_bytes((1024, 1024)),
    )
    engine.start()
    engine.generate(task([ref]))
    assert any("expected 1280x768" in note for note in engine.notes)


def test_config_rejects_an_unknown_engine():
    with pytest.raises(ConfigError):
        Config(engine="magic").validate()


def test_too_many_images_is_refused_before_the_request_is_sent(tmp_path):
    """A stop with three references is four inputs, and the API answers 400 after billing.

    This cost one wasted call during the first scene-1 render, so it is checked here
    instead: no request should reach the network at all.
    """
    refs = [make_ref(tmp_path / f"r{i}.png") for i in range(3)]
    start = make_ref(tmp_path / "start.jpg")
    post = FakePost(png_bytes())
    engine = ApiEngine(Config(engine="tokenplan", api_max_refs=3), poster=post, fetcher=lambda url: png_bytes())
    engine.start()
    with pytest.raises(RuntimeError) as exc:
        engine.generate(task([*refs, start], tag="stop"))
    assert "at most 3 images" in str(exc.value)
    assert "start.jpg" in str(exc.value)
    assert post.calls == [], "the guard must run before the request"
