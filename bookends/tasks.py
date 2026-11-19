"""Task resolution: turn shots into an ordered, reproducible list of jobs.

A task is fully self-describing -- prompt text, the exact ordered image list, a
seed derived from the shot name, and the output path -- so `prep` can print it,
`batch` can render it, and `check` can audit it without re-deriving anything.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .config import Config
from .prompt import START_FRAME_LABEL, compose
from .refs import select
from .shot import IMAGE_EXTS, Shot

TAGS = ("start", "stop")


@dataclass
class Task:
    shot: str
    tag: str
    prompt: str
    images: list[Path]
    seed: int
    out: Path
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.shot}:{self.tag}"

    def to_dict(self) -> dict:
        return {
            "shot": self.shot,
            "tag": self.tag,
            "out": str(self.out),
            "seed": self.seed,
            "images": [str(p) for p in self.images],
            "prompt_words": len(self.prompt.split()),
            "prompt": self.prompt,
            "notes": self.notes,
        }


def derive_seed(shot: str, tag: str, base: int = 42) -> int:
    """Deterministic seed: same shot and tag always reproduce, different ones differ.

    Re-rolling is therefore always an explicit act (`--force`, or a new
    `SEED_BASE`), never something that happens because a batch was re-run.
    """
    digest = hashlib.sha256(f"{base}:{shot}:{tag}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (2**31)


def global_references(cfg: Config) -> list[Path]:
    """Project-wide references, in a stable order, applied to every shot."""
    root = Path(cfg.reference_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def resolve(cfg: Config, shots: list[Shot]) -> tuple[list[Task], list[str]]:
    """Build every start/stop task for the given shots.

    Image order per task is: selected references, then start.jpg for a stop task in
    `sequential` mode. The stop task only picks up start.jpg if it is already on
    disk, which is why `batch` resolves again between its two phases -- see
    `bookends.__main__.cmd_batch`.
    """
    refs = global_references(cfg)
    tasks: list[Task] = []
    notes: list[str] = []

    if not refs:
        notes.append(
            f"no project references in {cfg.reference_dir}: every shot will rely on its own ref_* "
            f"images only"
        )

    for shot in shots:
        selection = select(cfg, shot)
        labels = dict(selection.labels)
        base_images = list(selection.images)
        for tag in TAGS:
            images = list(base_images)
            if tag == "stop" and cfg.pair_mode == "sequential":
                start_out = shot.root / "start.jpg"
                if start_out.is_file():
                    images.append(start_out)
                    labels.setdefault(start_out.name, START_FRAME_LABEL)
                else:
                    notes.append(
                        f"{shot.name}: start.jpg is absent, so this stop is planned without it; "
                        f"batch resolves stops again once the start frames have rendered"
                    )

            prompt, prompt_notes = compose(
                shot.text_for(tag),
                images,
                labels=labels,
                style=cfg.style,
                max_words=cfg.max_prompt_words,
            )
            tasks.append(
                Task(
                    shot=shot.name,
                    tag=tag,
                    prompt=prompt,
                    images=images,
                    seed=derive_seed(shot.name, tag, cfg.seed_base),
                    out=shot.root / f"{tag}.jpg",
                    notes=list(shot.warnings) + list(selection.notes) + prompt_notes,
                )
            )
    return tasks, notes


def pending(tasks: list[Task], force: bool = False) -> list[Task]:
    """Everything not already on disk. This is what makes re-rolls cheap."""
    return [t for t in tasks if force or not t.out.is_file()]


def render_order(tasks: list[Task], pair_mode: str) -> list[Task]:
    """Order work so a sequential stop never runs before its start.

    With `pair_mode=sequential` the stop task consumes `start.jpg`, so starts are
    emitted first across all shots; each GPU worker then walks the list in order.
    """
    if pair_mode != "sequential":
        return list(tasks)
    return sorted(tasks, key=lambda t: (0 if t.tag == "start" else 1, t.shot))


def write_tasks_json(tasks: list[Task], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "tasks": [t.to_dict() for t in tasks],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_tasks_json(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["tasks"]


def summarise(tasks: list[Task]) -> dict:
    words = [len(t.prompt.split()) for t in tasks] or [0]
    return {
        "tasks": len(tasks),
        "shots": len({t.shot for t in tasks}),
        "mean_prompt_words": round(sum(words) / len(words), 1),
        "max_prompt_words": max(words),
        "with_references": sum(1 for t in tasks if len(t.images) > 1),
        "noted": sum(1 for t in tasks if t.notes),
    }
