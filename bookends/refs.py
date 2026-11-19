"""Reference selection: which images go into a given generation.

The model card is specific -- multi-image editing is trained for 1 to 3 inputs and
"optimal performance is currently achieved with 1 to 3 input images". Passing a
whole project library into every shot therefore costs quality, not just time. So
references are *selected*:

  shots/work/shot_007/use.txt     per-shot, ordered, wins
  reference/default.txt           project-wide default list
  (neither)                       shot-local refs + plates, plus at most
                                  DEFAULT_MAX_REFS globals, with a note

Format, one filename per line, `#` comments allowed:

    char_front.png
    plate_garden.png
    -ref_crowd.png          # a leading '-' excludes a default-included file

Slot order is: the named files in the order written, then any shot-local `ref_*`
not already named, then `plate_*`. That order is what the composed prompt's
"Image 1 shows ..." numbering refers to, so it is deterministic and visible in
`bookends prep`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .prompt import load_labels
from .shot import IMAGE_EXTS, Shot

# Qwen-Image-Edit-2509 is trained for multi-image editing with 1-3 inputs.
DEFAULT_MAX_REFS = 3
MANIFEST_NAME = "manifest.sha256"


class SelectionError(ValueError):
    """A selection names something that does not exist: fail before spending GPU time."""


@dataclass
class Selection:
    images: list[Path]
    labels: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def images_in(folder: Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def read_list(path: Path) -> list[str] | None:
    """Parse a selection file. Returns None when the file does not exist."""
    path = Path(path)
    if not path.is_file():
        return None
    entries: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def _find(name: str, shot: Shot, reference_dir: Path) -> Path | None:
    """Resolve a bare filename: the shot's own files take precedence over shared ones."""
    for root in (shot.root, reference_dir):
        candidate = Path(root) / name
        if candidate.is_file():
            return candidate
    return None


def select(cfg: Config, shot: Shot, *, max_refs: int = DEFAULT_MAX_REFS) -> Selection:
    reference_dir = Path(cfg.reference_dir)
    entries = read_list(shot.root / "use.txt")
    source = shot.root / "use.txt"
    if entries is None:
        entries = read_list(reference_dir / "default.txt")
        source = reference_dir / "default.txt"

    excluded = {e[1:] for e in entries if e.startswith("-")} if entries else set()
    listed = [e for e in entries if not e.startswith("-")] if entries else []
    notes: list[str] = []

    if entries is not None and listed:
        chosen: list[Path] = []
        for name in listed:
            found = _find(name, shot, reference_dir)
            if found is None:
                pool = [p.name for p in images_in(reference_dir) + shot.refs + shot.plates]
                raise SelectionError(
                    f"{shot.name}: {source.name} names {name!r}, which is not in the shot or in "
                    f"{reference_dir}. Available: {', '.join(pool) or '(none)'}"
                )
            if found not in chosen:
                chosen.append(found)
    else:
        pool = images_in(reference_dir)
        chosen = [p for p in pool if p.name not in excluded]
        if len(chosen) > max_refs:
            notes.append(
                f"{shot.name}: {len(chosen)} project references and no selection; using the first "
                f"{max_refs} ({', '.join(p.name for p in chosen[:max_refs])}). Add use.txt or "
                f"reference/default.txt to choose deliberately -- 1-3 inputs is where this model is "
                f"strongest"
            )
            chosen = chosen[:max_refs]
        elif not chosen and not shot.refs and not shot.plates:
            notes.append(f"{shot.name}: no references available at all")

    seen = {p.name for p in chosen}
    for extra in list(shot.refs) + list(shot.plates):
        if extra.name in excluded or extra.name in seen:
            continue
        chosen.append(extra)
        seen.add(extra.name)

    if len(chosen) > max_refs:
        notes.append(
            f"{shot.name}: {len(chosen)} images go into this generation "
            f"({', '.join(p.name for p in chosen)}); above {max_refs} the model's multi-image "
            f"conditioning degrades"
        )

    labels = dict(load_labels(reference_dir))
    labels.update(load_labels(shot.root))
    return Selection(images=chosen, labels=labels, notes=notes)


# --------------------------------------------------------------------- manifest
def manifest_path(cfg: Config) -> Path:
    return Path(cfg.reference_dir) / MANIFEST_NAME


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_entries(cfg: Config) -> dict[str, str]:
    """Every file under reference/ except the manifest itself.

    Labels (refs.json) are included on purpose: they are an input to every prompt
    in the project, so changing one silently rewrites all of them.
    """
    root = Path(cfg.reference_dir)
    if not root.is_dir():
        return {}
    entries: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != MANIFEST_NAME:
            entries[path.relative_to(root).as_posix()] = sha256_file(path)
    return entries


def write_manifest(cfg: Config) -> tuple[Path, int]:
    entries = _manifest_entries(cfg)
    path = manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{digest}  {name}" for name, digest in sorted(entries.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path, len(entries)


def verify_manifest(cfg: Config) -> tuple[list[str], int]:
    """Compare reference/ against manifest.sha256. Returns (problems, checked)."""
    path = manifest_path(cfg)
    actual = _manifest_entries(cfg)
    if not path.is_file():
        if not actual:
            return [], 0
        return [f"no {MANIFEST_NAME} in {Path(cfg.reference_dir)}: {len(actual)} file(s) unverified; "
                f"run `bookends refs --write-manifest` on the machine that owns the references"], 0

    expected: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition("  ")
        if digest and name:
            expected[name] = digest

    problems: list[str] = []
    for name, digest in sorted(expected.items()):
        if name not in actual:
            problems.append(f"missing: {name}")
        elif actual[name] != digest:
            problems.append(f"changed: {name}")
    for name in sorted(actual):
        if name not in expected:
            problems.append(f"unlisted: {name} (added after the manifest was written)")
    return problems, len(expected)
