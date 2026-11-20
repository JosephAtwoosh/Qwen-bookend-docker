"""`auto` must be resolved before the engine applies it.

The first real benchmark spent 8 min 15 s per image because `PROFILE=auto` reached
QwenEngine._apply_profile unresolved, fell through to the "nothing was decided"
branch, and selected sequential CPU offload on a card with 32 GB of VRAM.
"""
from __future__ import annotations

from bookends import hardware
from bookends.config import Config


def test_auto_resolves_to_the_recommended_profile(monkeypatch):
    monkeypatch.setattr(hardware, "recommend", lambda cfg: {"profile": "fp8-t2i-cpu", "workers": 1})
    assert hardware.resolve_profile(Config(profile="auto")) == "fp8-t2i-cpu"


def test_explicit_profile_is_never_overridden(monkeypatch):
    calls = []
    monkeypatch.setattr(hardware, "recommend", lambda cfg: calls.append(cfg) or {"profile": "fp8-t2i-cpu"})
    assert hardware.resolve_profile(Config(profile="bf16-offload")) == "bf16-offload"
    assert calls == [], "an explicit profile must not trigger hardware probing"


def test_batch_reports_the_profile_it_will_actually_use(monkeypatch, capsys, tmp_path):
    """The printed profile is the resolved one, not the literal 'auto'."""
    from bookends.__main__ import main
    from tests.test_cli import WIDTH, HEIGHT, build_batch, base_args

    paths = build_batch(tmp_path, names=("shot_001",))
    monkeypatch.setattr(
        hardware, "recommend", lambda cfg: {"profile": "fp8-t2i-cpu", "workers": 1, "reasons": [], "warnings": [], "gpus": [], "ram_gb": 500, "vram_gb": 32}
    )
    assert main(base_args(paths, "batch", "--fake-pipe", "--profile", "auto")) == 0
    out = capsys.readouterr().out
    assert "profile fp8-t2i-cpu" in out
    assert "profile auto" not in out
