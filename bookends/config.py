"""Configuration: env vars, defaults, validation.

Everything the generator needs to know is here, and everything here is either
an env var or a CLI flag. Nothing is read from a hidden location, so an image
can always be explained by the sidecar JSON that `pipe` writes next to it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Profile = how the 40.9 GB transformer and the 16.6 GB text encoder are placed.
#
#   fp8-dual      both quantised to fp8, both resident on the GPU (~27.5 GB measured).
#                 The only fast profile that works: it never touches the offload
#                 machinery, which torchao <0.15 cannot survive.
#   fp8-offload   fp8 transformer with model CPU offload. Needs torchao >= 0.15.
#   bf16-offload  sequential CPU offload; always works, ~8 minutes per image.
#
# bf16 for both does not fit in 24 GB, and pinning the text encoder to the CPU while
# the transformer sits on the GPU is not supported by QwenImageEditPlusPipeline.
PROFILES = ("auto", "fp8-dual", "fp8-offload", "bf16-offload")
PAIR_MODES = ("sequential", "independent")

DEFAULT_STYLE_SUFFIX = (
    "Keep the subject's proportions, colours and design exactly as in the reference images."
)
NO_TEXT_CLAUSE = "No text, lettering, captions, watermarks or speech bubbles anywhere in the picture."


class ConfigError(ValueError):
    """A hard configuration error: stop, do not generate."""


@dataclass
class Config:
    # model
    model_id: str = "Qwen/Qwen-Image-Edit-2509"
    revision: str | None = None
    profile: str = "auto"

    # sampling
    width: int = 1280
    height: int = 768
    steps: int = 28
    true_cfg_scale: float = 4.0
    guidance_scale: float = 1.0
    negative_prompt: str = " "
    seed_base: int = 42

    # output
    jpeg_quality: int = 95
    pair_mode: str = "sequential"
    style: str | None = None
    max_prompt_words: int = 120

    # execution
    workers: int = 0  # 0 = decide from hardware

    # paths (relative defaults so a local run works; the image sets absolutes)
    shots_dir: Path = Path("shots")
    reference_dir: Path = Path("reference")
    output_dir: Path = Path("output")
    models_dir: Path = Path("models")

    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ paths
    @property
    def in_dir(self) -> Path:
        return self.shots_dir / "in"

    @property
    def work_dir(self) -> Path:
        return self.shots_dir / "work"

    def ensure_dirs(self) -> None:
        for p in (self.in_dir, self.work_dir, self.output_dir, self.reference_dir):
            p.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        """Raise on anything that would waste GPU time; return soft warnings."""
        errors: list[str] = []
        if self.width % 64 or self.height % 64:
            errors.append(
                f"width/height must be multiples of 64 (LTX grid), got {self.width}x{self.height}"
            )
        if self.width < 256 or self.height < 256:
            errors.append(f"refusing to render below 256px a side, got {self.width}x{self.height}")
        if not (1 <= self.steps <= 150):
            errors.append(f"steps out of range 1..150: {self.steps}")
        if self.workers < 0:
            errors.append(f"workers must be >= 0, got {self.workers}")
        if self.pair_mode not in PAIR_MODES:
            errors.append(f"pair_mode must be one of {PAIR_MODES}, got {self.pair_mode!r}")
        if self.profile not in PROFILES:
            errors.append(f"profile must be one of {PROFILES}, got {self.profile!r}")
        if not (1 <= self.jpeg_quality <= 100):
            errors.append(f"jpeg_quality out of range: {self.jpeg_quality}")

        warnings: list[str] = []
        pixels = self.width * self.height
        if pixels > 1_600_000:
            warnings.append(
                f"{self.width}x{self.height} is {pixels / 1e6:.2f}MP; Qwen-Image-Edit is trained "
                f"around 1MP and 24 GB cards will be tight"
            )
        if self.max_prompt_words > 200:
            warnings.append(
                f"max_prompt_words={self.max_prompt_words} is permissive; the take-2 post-mortem "
                f"blames 505-word prompts for the model ignoring the shot"
            )
        if errors:
            raise ConfigError("; ".join(errors))
        return warnings

    # ------------------------------------------------------------------- env
    @classmethod
    def from_env(cls, **overrides) -> "Config":
        env = os.environ
        cfg = cls(
            model_id=env.get("MODEL_ID", cls.model_id),
            revision=env.get("MODEL_REVISION") or None,
            profile=env.get("PROFILE", cls.profile),
            width=_int(env, "WIDTH", cls.width),
            height=_int(env, "HEIGHT", cls.height),
            steps=_int(env, "STEPS", cls.steps),
            true_cfg_scale=_float(env, "TRUE_CFG_SCALE", cls.true_cfg_scale),
            guidance_scale=_float(env, "GUIDANCE_SCALE", cls.guidance_scale),
            negative_prompt=env.get("NEGATIVE_PROMPT", cls.negative_prompt),
            seed_base=_int(env, "SEED_BASE", cls.seed_base),
            jpeg_quality=_int(env, "JPEG_QUALITY", cls.jpeg_quality),
            pair_mode=env.get("PAIR_MODE", cls.pair_mode),
            style=env.get("STYLE") or None,
            max_prompt_words=_int(env, "MAX_PROMPT_WORDS", cls.max_prompt_words),
            workers=_int(env, "WORKERS", cls.workers),
            shots_dir=Path(env.get("SHOTS_DIR", "shots")),
            reference_dir=Path(env.get("REFERENCE_DIR", "reference")),
            output_dir=Path(env.get("OUTPUT_DIR", "output")),
            models_dir=Path(env.get("MODELS_DIR", "models")),
        )
        for key, value in overrides.items():
            if value is None:
                continue
            if not hasattr(cfg, key):
                raise ConfigError(f"unknown config key {key!r}")
            setattr(cfg, key, value)
        cfg.validate()
        return cfg

    def describe(self) -> dict:
        d = asdict(self)
        for k in ("shots_dir", "reference_dir", "output_dir", "models_dir"):
            d[k] = str(d[k])
        return d


def _int(env, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def _float(env, name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
