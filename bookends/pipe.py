"""Engines: the thing that turns a task into a PIL image.

Two implementations behind one interface, for one reason: the whole loop has to
be runnable on a machine with no GPU, so CI and a laptop can prove that task
resolution, skipping, review and packing all work. `FakeEngine` is that path and
is what `--fake-pipe` uses.

`QwenEngine` is the real one. It is the only module that imports torch, and it
only does so inside `start()`.

Profile notes (see docs/VRAM.md for the measurements):

  fp8-t2i-cpu    transformer fp8 on the GPU, text encoder on the CPU
  fp8-offload    transformer fp8, every component swapped in on demand
  bf16-offload   sequential CPU offload; slow, but runs on anything

fp8 is not free: converting in place needs the whole 40.9 GB bf16 transformer
resident in RAM first. `hardware.recommend()` refuses the fp8 profiles below
~48 GB RAM for that reason.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image

from .config import Config

REQUIRED_SIDE = ("width", "height")


class Engine:
    """Interface: start once, generate many, close once."""

    name = "engine"

    def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def generate(self, task) -> Image.Image:  # pragma: no cover - interface
        raise NotImplementedError

    def info(self) -> dict:
        return {"engine": self.name}

    def close(self) -> None:
        return None


class FakeEngine(Engine):
    """Deterministic stand-in: same task in, same picture out, no torch.

    The picture is a coarse gradient with a few soft blobs, seeded from the
    prompt and seed. Two different tasks give measurably different images, so
    the review metrics have something real to compute.
    """

    name = "fake"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def start(self) -> None:
        return None

    def generate(self, task) -> Image.Image:
        import numpy as np

        digest = hashlib.sha256(f"{task.prompt}|{task.seed}".encode("utf-8")).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        w, h = self.cfg.width, self.cfg.height

        xs, ys = np.meshgrid(
            np.linspace(0, 1, w, dtype=np.float32),
            np.linspace(0, 1, h, dtype=np.float32),
        )
        base = np.stack(
            [
                0.35 + 0.5 * xs + 0.15 * rng.random(),
                0.30 + 0.4 * ys + 0.20 * rng.random(),
                0.45 + 0.3 * (1 - xs) + 0.15 * rng.random(),
            ],
            axis=-1,
        )
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) / max(w, h)
        for _ in range(3):
            cx, cy = rng.random(), rng.random()
            r = 0.08 + 0.18 * rng.random()
            mask = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))[..., None]
            colour = rng.random(3).astype(np.float32)
            base = base * (1 - 0.7 * mask) + colour * (0.7 * mask)
        arr = (np.clip(base, 0, 1) * 255).astype(np.uint8)
        return Image.fromarray(arr, mode="RGB")

    def info(self) -> dict:
        return {"engine": self.name, "note": "images are synthetic; not model output"}


class QwenEngine(Engine):
    """Qwen-Image-Edit-2509 via diffusers, with the profile applied."""

    name = "qwen-image-edit"

    def __init__(self, cfg: Config, device_index: int = 0):
        self.cfg = cfg
        self.device_index = device_index
        self.pipe = None
        self.used_profile = cfg.profile
        self.notes: list[str] = []
        self._torch = None

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        import torch  # noqa: F401  (deliberately late)
        from diffusers import QwenImageEditPlusPipeline

        self._torch = torch
        if torch.cuda.is_available():
            torch.cuda.set_device(self.device_index)
        device = f"cuda:{self.device_index}" if torch.cuda.is_available() else "cpu"

        self.pipe = QwenImageEditPlusPipeline.from_pretrained(
            self.cfg.model_id,
            revision=self.cfg.revision,
            torch_dtype=torch.bfloat16,
        )
        self.pipe.set_progress_bar_config(disable=True)
        self._apply_profile(device)

    def _apply_profile(self, device: str) -> None:
        torch = self._torch
        # "auto" must be resolved against this box before anything else: it is not a
        # profile the engine can apply, and treating it as one silently selected
        # sequential CPU offload (measured 495 s/image vs ~19 GB VRAM for fp8).
        from .hardware import resolve_profile

        want = resolve_profile(self.cfg)

        if want in ("fp8-dual", "fp8-t2i-cpu", "fp8-offload"):
            if not self._try_quantise_fp8():
                want = "bf16-offload"
        elif want != "bf16-offload":
            want = "bf16-offload"

        if want in ("fp8-dual", "fp8-t2i-cpu"):
            # Measured shape that works on a 32 GB Ada card: quantise the text encoder
            # as well and keep everything resident. No offload, therefore no
            # `_has_compatible_shallow_copy_type`, which torchao 0.9 does not implement.
            if self._try_quantise_text_encoder():
                self.used_profile = "fp8-dual"
                self.pipe.transformer.to(device)
                self.pipe.vae.to(device)
                self.pipe.text_encoder.to(device)
                self.notes.append("transformer and text encoder both fp8, resident on the GPU")
            else:
                self.used_profile = "bf16-offload"
                self.notes.append("text encoder quantisation failed; falling back to bf16 offload")
        elif want == "fp8-offload":
            self.used_profile = "fp8-offload"
            if torch.cuda.is_available():
                torch.cuda.set_device(self.device_index)
            self.pipe.enable_model_cpu_offload()
            self.notes.append(
                "fp8 transformer with model CPU offload (needs torchao >= 0.15; older "
                "torchao raises NotImplementedError on shallow_copy)"
            )
        else:
            self.used_profile = "bf16-offload"
            if torch.cuda.is_available():
                torch.cuda.set_device(self.device_index)
            self.pipe.enable_sequential_cpu_offload()
            self.notes.append("sequential CPU offload (slow, works on small VRAM)")

    def _try_quantise_text_encoder(self) -> bool:
        try:
            from torchao.quantization import float8_weight_only, quantize_

            quantize_(self.pipe.text_encoder, float8_weight_only())
            return True
        except Exception as exc:  # noqa: BLE001
            self.notes.append(
                f"text encoder fp8 failed ({type(exc).__name__}: {exc}); falling back"
            )
            return False

    def _try_quantise_fp8(self) -> bool:
        try:
            from torchao.quantization import float8_weight_only, quantize_
        except Exception as exc:  # torchao absent or built for another torch
            self.notes.append(f"fp8 unavailable ({type(exc).__name__}: {exc}); falling back to bf16 offload")
            return False
        try:
            quantize_(self.pipe.transformer, float8_weight_only())
            self.notes.append("transformer quantised to fp8 (weight-only)")
            return True
        except Exception as exc:
            self.notes.append(f"fp8 quantisation failed ({type(exc).__name__}: {exc}); falling back")
            return False

    # --------------------------------------------------------------- generate
    def generate(self, task) -> Image.Image:
        torch = self._torch
        self.pipe.set_progress_bar_config(disable=True)

        images = []
        for path in task.images:
            with Image.open(path) as im:
                images.append(im.convert("RGB"))

        device = f"cuda:{self.device_index}" if torch.cuda.is_available() else "cpu"
        generator = torch.Generator(device=device).manual_seed(task.seed)

        with torch.inference_mode():
            result = self.pipe(
                image=images if images else None,
                prompt=task.prompt,
                negative_prompt=self.cfg.negative_prompt,
                width=self.cfg.width,
                height=self.cfg.height,
                num_inference_steps=self.cfg.steps,
                true_cfg_scale=self.cfg.true_cfg_scale,
                guidance_scale=self.cfg.guidance_scale,
                num_images_per_prompt=1,
                generator=generator,
            )
        image = result.images[0]
        if image.size != (self.cfg.width, self.cfg.height):
            # The LTX grid is not negotiable: a bookend must be exactly WxH.
            self.notes.append(
                f"model returned {image.size[0]}x{image.size[1]}; resized to "
                f"{self.cfg.width}x{self.cfg.height}"
            )
            image = image.resize((self.cfg.width, self.cfg.height), Image.LANCZOS)
        return image

    def info(self) -> dict:
        info = {"engine": self.name, "requested_profile": self.cfg.profile, "used_profile": self.used_profile}
        info["notes"] = list(self.notes)
        try:
            import torch
            import diffusers

            info["torch"] = torch.__version__
            info["diffusers"] = diffusers.__version__
            if torch.cuda.is_available():
                info["device"] = torch.cuda.get_device_name(self.device_index)
                info["device_memory_gb"] = round(
                    torch.cuda.get_device_properties(self.device_index).total_memory / 1024**3, 1
                )
                info["peak_vram_gb"] = round(torch.cuda.max_memory_allocated(self.device_index) / 1024**3, 2)
        except Exception as exc:  # pragma: no cover - diagnostics only
            info["version_error"] = str(exc)
        return info

    def close(self) -> None:
        torch = self._torch
        self.pipe = None
        if torch is None:
            return
        try:
            torch.cuda.empty_cache()
        except Exception:  # pragma: no cover - best effort
            pass


def make_engine(cfg: Config, device_index: int = 0, fake: bool = False) -> Engine:
    return FakeEngine(cfg) if fake else QwenEngine(cfg, device_index)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sidecar_payload(cfg: Config, task, engine: Engine, seconds: float, output: Image.Image) -> dict:
    """Everything needed to explain or reproduce this image later."""
    return {
        "shot": task.shot,
        "tag": task.tag,
        "output": str(task.out),
        "output_size": list(output.size),
        "output_sha256": hashlib.sha256(output.tobytes()).hexdigest()[:16],
        "prompt": task.prompt,
        "prompt_words": len(task.prompt.split()),
        "seed": task.seed,
        "images": [{"path": str(p), "sha256": sha256_file(p)} for p in task.images],
        "sampling": {
            "steps": cfg.steps,
            "true_cfg_scale": cfg.true_cfg_scale,
            "guidance_scale": cfg.guidance_scale,
            "negative_prompt": cfg.negative_prompt,
            "width": cfg.width,
            "height": cfg.height,
            "jpeg_quality": cfg.jpeg_quality,
        },
        "engine": engine.info(),
        "seconds": round(seconds, 2),
        "notes": list(task.notes),
    }


def s_per_image(seconds_list: list[float]) -> dict:
    if not seconds_list:
        return {"n": 0}
    ordered = sorted(seconds_list)
    mean = sum(ordered) / len(ordered)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "n": len(ordered),
        "mean_s": round(mean, 2),
        "median_s": round(median, 2),
        "min_s": round(ordered[0], 2),
        "max_s": round(ordered[-1], 2),
        "images_per_hour_1gpu": round(3600 / mean, 1) if mean > 0 else math.inf,
    }
