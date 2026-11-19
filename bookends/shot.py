"""The shot contract: what a shot folder is, and how a tarball becomes one.

A shot is a directory (or a tarball that extracts to one) containing:

    start.txt    required   what frame 0 shows
    stop.txt     required   what the last frame shows
    prompt.txt   required   the LTX motion prompt (carried through, not used here)
    audio.wav    optional   carried through to the handoff
    ref_*.png    optional   shot-specific references (Tripo renders, prop sheets)
    plate_*.png  optional   empty background plates for the set

Nothing else is required, and nothing else is trusted: tarball members are
checked before extraction.
"""
from __future__ import annotations

import re
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
REQUIRED = ("start.txt", "stop.txt", "prompt.txt")
OPTIONAL_AUDIO = ("audio.wav",)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ShotError(Exception):
    """A shot is unusable, and saying so now is cheaper than a bad render."""


@dataclass
class Shot:
    name: str
    root: Path
    start_text: str
    stop_text: str
    motion_text: str
    audio: Path | None = None
    refs: list[Path] = field(default_factory=list)
    plates: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def text_for(self, tag: str) -> str:
        if tag == "start":
            return self.start_text
        if tag == "stop":
            return self.stop_text
        raise ValueError(f"unknown tag {tag!r}")

    def to_dict(self) -> dict:
        return {
            "shot": self.name,
            "root": str(self.root),
            "refs": [p.name for p in self.refs],
            "plates": [p.name for p in self.plates],
            "audio": self.audio.name if self.audio else None,
            "start_words": len(self.start_text.split()),
            "stop_words": len(self.stop_text.split()),
            "warnings": list(self.warnings),
        }


def sanitise_name(raw: str) -> str:
    """Turn an arbitrary tarball stem into a directory name we are willing to use."""
    stem = raw
    for suffix in (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    if not cleaned:
        raise ShotError(f"cannot derive a shot name from {raw!r}")
    if not _SAFE_NAME.match(cleaned):
        raise ShotError(f"shot name {cleaned!r} is not a safe directory name")
    return cleaned


def _members_are_safe(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tar.getmembers():
        name = member.name.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise ShotError(f"tarball member has an absolute path: {member.name!r}")
        parts = Path(name).parts
        if ".." in parts:
            raise ShotError(f"tarball member escapes the extraction root: {member.name!r}")
        if member.issym() or member.islnk():
            # Links are never needed by the contract and are the classic escape hatch.
            raise ShotError(f"tarball contains a link, which is not allowed: {member.name!r}")
        target = (dest / name).resolve()
        if dest != target and dest not in target.parents:
            raise ShotError(f"tarball member escapes the extraction root: {member.name!r}")


def extract_shot(tar_path: Path, work_dir: Path, force: bool = False) -> Path:
    """Extract one tarball into work_dir/<shot>, returning the shot directory."""
    tar_path = Path(tar_path)
    work_dir = Path(work_dir)
    shot_dir = work_dir / sanitise_name(tar_path.name)
    if shot_dir.exists():
        if not force:
            return shot_dir
        shutil.rmtree(shot_dir)
    shot_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tar_path) as tar:
        _members_are_safe(tar, shot_dir)
        try:
            tar.extractall(shot_dir, filter="data")  # py>=3.12
        except TypeError:  # pragma: no cover - older interpreters
            tar.extractall(shot_dir)

    # A tarball that wraps everything in one folder is common enough to support.
    children = [p for p in shot_dir.iterdir() if p.is_dir()]
    has_docs = any((shot_dir / f).exists() for f in REQUIRED)
    if not has_docs and len(children) == 1:
        inner = children[0]
        for item in list(inner.iterdir()):
            shutil.move(str(item), str(shot_dir / item.name))
        inner.rmdir()
    return shot_dir


def load_shot(root: Path) -> Shot:
    """Read and validate one shot directory."""
    root = Path(root)
    if not root.is_dir():
        raise ShotError(f"not a directory: {root}")

    missing = [f for f in REQUIRED if not (root / f).is_file()]
    if missing:
        raise ShotError(f"{root.name}: missing required file(s): {', '.join(missing)}")

    def read(name: str) -> str:
        text = (root / name).read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise ShotError(f"{root.name}: {name} is empty")
        return text

    start_text, stop_text, motion_text = read("start.txt"), read("stop.txt"), read("prompt.txt")

    audio = next((root / n for n in OPTIONAL_AUDIO if (root / n).is_file()), None)
    refs = _images(root, "ref_")
    plates = _images(root, "plate_")

    warnings: list[str] = []
    if not refs and not plates:
        warnings.append(
            "no ref_* or plate_* images: generation would be text-only, which Qwen-Image-Edit "
            "will happily answer with an unrelated subject"
        )
    if start_text.lower() == stop_text.lower():
        warnings.append("start.txt and stop.txt are identical: the shot has nothing to animate")

    return Shot(
        name=root.name,
        root=root,
        start_text=start_text,
        stop_text=stop_text,
        motion_text=motion_text,
        audio=audio,
        refs=refs,
        plates=plates,
        warnings=warnings,
    )


def _images(root: Path, prefix: str) -> list[Path]:
    return sorted(
        p
        for p in root.iterdir()
        if p.is_file() and p.name.startswith(prefix) and p.suffix.lower() in IMAGE_EXTS
    )


def discover_tarballs(in_dir: Path) -> list[Path]:
    in_dir = Path(in_dir)
    if not in_dir.is_dir():
        return []
    found = [p for p in in_dir.iterdir() if p.is_file() and _looks_like_tarball(p.name)]
    return sorted(found)


def _looks_like_tarball(name: str) -> bool:
    low = name.lower()
    return low.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar"))


def discover_shots(work_dir: Path) -> list[Path]:
    work_dir = Path(work_dir)
    if not work_dir.is_dir():
        return []
    return sorted(p for p in work_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
