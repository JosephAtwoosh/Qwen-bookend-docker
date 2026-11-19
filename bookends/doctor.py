"""Doctor: prove the box and the config before spending GPU money.

Mirrors the `doctor.sh` habit from ltx25-docker. Everything it prints is measured
on the box in front of it -- no assumptions about the card, the RAM or the
weights, because those assumptions are exactly what went wrong in the planning
thread (a 40.9 GB transformer was assumed to fit on a 24 GB card).
"""
from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

from . import hardware
from .config import Config


def collect(cfg: Config) -> dict:
    warnings: list[str] = []
    errors: list[str] = []

    try:
        cfg.validate()
    except Exception as exc:
        errors.append(str(exc))

    hw = hardware.recommend(cfg)
    warnings.extend(hw.get("warnings", []))

    dirs = {
        "shots": Path(cfg.shots_dir),
        "reference": Path(cfg.reference_dir),
        "output": Path(cfg.output_dir),
        "models": Path(cfg.models_dir),
    }
    dir_status = {}
    for name, path in dirs.items():
        exists = path.exists()
        writable = False
        if exists:
            try:
                probe = path / ".bookends-write-probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                writable = True
            except OSError:
                writable = False
        dir_status[name] = {"path": str(path), "exists": exists, "writable": writable}
        if name in ("output",) and not exists:
            warnings.append(f"{name} directory does not exist yet: {path}")

    cache_gb = hardware.model_cache_size_gb(cfg.models_dir)
    if cache_gb == 0:
        warnings.append(
            f"no weights under {cfg.models_dir}: run `scripts/run.sh download` first "
            f"(~58 GB for Qwen-Image-Edit-2509)"
        )

    runtime = _runtime_versions()
    if not runtime.get("torch"):
        warnings.append("torch is not importable: this interpreter can prep/check/pack but not generate")
    if not runtime.get("diffusers"):
        warnings.append("diffusers is not importable: generation unavailable")

    if cfg.width % 64 or cfg.height % 64:
        errors.append(f"output size {cfg.width}x{cfg.height} is off the 64 grid LTX needs")

    return {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "config": cfg.describe(),
        "hardware": hw,
        "runtime": runtime,
        "dirs": dir_status,
        "model_cache_gb": cache_gb,
        "expected_weights_gb": round(
            hardware.TRANSFORMER_BF16_GB + hardware.TEXT_ENCODER_BF16_GB + hardware.VAE_GB, 1
        ),
        "ffmpeg": shutil.which("ffmpeg"),
        "warnings": warnings,
        "errors": errors,
    }


def _runtime_versions() -> dict:
    out = {}
    for name in ("torch", "diffusers", "transformers", "torchao"):
        try:
            module = __import__(name)
            out[name] = getattr(module, "__version__", "unknown")
        except Exception:
            out[name] = None
    return out


def format_text(report: dict) -> str:
    hw = report["hardware"]
    runtime = report["runtime"]
    lines = [
        "bookends doctor",
        "===============",
        f"python        {report['python']}   {report['platform']}",
        f"ffmpeg        {report['ffmpeg'] or 'not found'}",
        "",
        "hardware",
        f"  GPUs        {hw['gpus'] or 'none visible'}",
        f"  RAM         {hw['ram_gb'] if hw['ram_gb'] is not None else 'unknown'} GB "
        f"(free {hw['free_ram_gb'] if hw['free_ram_gb'] is not None else '?'} GB)",
        f"  profile     {hw['profile']}   workers {hw['workers']}",
    ]
    for reason in hw["reasons"]:
        lines.append(f"    - {reason}")
    lines += [
        "",
        "weights",
        f"  cache       {report['model_cache_gb']} GB under {report['config']['models_dir']}",
        f"  expected    {report['expected_weights_gb']} GB for the full checkpoint",
        "",
        "runtime",
        *[f"  {k:<12}{v or 'not importable'}" for k, v in runtime.items()],
        "",
        "dirs",
        *[
            f"  {name:<11}{info['path']}  "
            f"{'ok' if info['writable'] else ('missing' if not info['exists'] else 'not writable')}"
            for name, info in report["dirs"].items()
        ],
        "",
        "config",
        f"  size        {report['config']['width']}x{report['config']['height']}  "
        f"steps {report['config']['steps']}  cfg {report['config']['true_cfg_scale']}",
        f"  pair mode   {report['config']['pair_mode']}   style {report['config']['style']}",
    ]
    if report["warnings"]:
        lines += ["", "warnings"] + [f"  ! {w}" for w in report["warnings"]]
    if report["errors"]:
        lines += ["", "errors"] + [f"  x {e}" for e in report["errors"]]
    if not report["warnings"] and not report["errors"]:
        lines += ["", "no warnings"]
    return "\n".join(lines)
