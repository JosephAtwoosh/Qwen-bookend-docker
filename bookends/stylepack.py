"""Style & prop bank.

The reason this repo exists: you rent a GPU for an hour, generate a look once, and
never pay to download Qwen's weights again. A style bank turns one set of
reference renders into a reusable library of the same subject in several
treatments, at exactly the size LTX will ask for.

It is also the cheapest way to find out which look the project wants, because a
sweep is one batch and the review pack shows all of them side by side.
"""
from __future__ import annotations

import re
import tarfile
from pathlib import Path

from .config import Config
from .prompt import STYLES, compose, default_label, load_labels
from .shot import IMAGE_EXTS
from .tasks import Task, derive_seed


def references(cfg: Config) -> list[Path]:
    root = Path(cfg.reference_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def resolve_styles(styles: list[str] | None) -> list[str]:
    if not styles:
        return list(STYLES)
    out = []
    for raw in styles:
        key = raw.strip()
        if not key:
            continue
        out.append(key.lower() if key.lower() in STYLES else key)
    return out


def _safe(style: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", style.lower()).strip("_") or "style"


def archive(out_dir: Path, name: str = "style_bank") -> Path:
    """Tar the bank so it can be pulled off the box in one file before shutdown."""
    out_dir = Path(out_dir)
    src = out_dir / name
    bundle = out_dir / f"{name}.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(src, arcname=name)
    return bundle


def build_tasks(
    cfg: Config,
    *,
    styles: list[str] | None = None,
    extra_prompt: str = "",
    seed: int | None = None,
    all_refs: bool = False,
    out_dir: Path | None = None,
) -> tuple[list[Task], list[str]]:
    """One task per (style, reference) pair."""
    refs = references(cfg)
    if not refs:
        raise ValueError(
            f"no reference images in {cfg.reference_dir}: put the base renders (Tripo, prop "
            f"sheets, character turnarounds) there first"
        )
    out_root = (Path(out_dir) if out_dir else Path(cfg.output_dir)) / "style_bank"
    labels = load_labels(cfg.reference_dir)

    tasks: list[Task] = []
    notes: list[str] = []
    for style in resolve_styles(styles):
        body = (STYLES.get(style, style) + ". " + extra_prompt).strip()
        for ref in refs:
            images = list(refs) if all_refs else [ref]
            body_full = body if all_refs else (
                f"Image 1 shows {labels.get(ref.name) or default_label(ref)}. {body}"
            )
            prompt, prompt_notes = compose(body_full, images, labels=labels, style=None, max_words=cfg.max_prompt_words)
            name = f"{style}_{ref.stem}"
            tasks.append(
                Task(
                    shot=name,
                    tag="style",
                    prompt=prompt,
                    images=images,
                    seed=seed if seed is not None else derive_seed(name, "style", cfg.seed_base),
                    out=out_root / _safe(style) / f"{ref.stem}_{_safe(style)}.jpg",
                    notes=prompt_notes,
                )
            )
    if len(refs) > 4 and not all_refs:
        notes.append(
            f"{len(refs)} references x {len(tasks) // max(1, len(refs))} styles = {len(tasks)} images; "
            f"use --styles to narrow a first sweep"
        )
    return tasks, notes
