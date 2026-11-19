"""Shared fixtures: a shot factory and a tarball factory.

Everything here uses Pillow only, so the whole suite runs without torch.
"""
from __future__ import annotations

import io
import re
import tarfile
import tempfile
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Some sandboxes deny DELETE on the system temp directory, which breaks pytest's
# tmp_path (it creates a numbered dir and cleans up). Detect that once and fall
# back to a per-session directory inside the repo, so the suite runs anywhere
# without special flags. On a normal Linux/CI box this block does nothing.
# ---------------------------------------------------------------------------
_RUN = uuid.uuid4().hex[:6]
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _system_temp_supports_cleanup() -> bool:
    try:
        d = Path(tempfile.mkdtemp())
        probe = d / "probe.txt"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        d.rmdir()
        return True
    except OSError:
        return False


if not _system_temp_supports_cleanup():

    @pytest.fixture
    def tmp_path(request):  # noqa: F811 - deliberate override
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-100:]
        root = _REPO_ROOT / ".test-tmp" / f"{slug}-{_RUN}"
        root.mkdir(parents=True, exist_ok=True)
        return root


def write_image(
    path: Path,
    *,
    size: tuple[int, int] = (640, 384),
    top: tuple[int, int, int] = (120, 160, 210),
    bottom: tuple[int, int, int] = (40, 60, 90),
    sun: tuple[int, int] | None = None,
) -> Path:
    """A cheap but non-trivial image: a vertical gradient plus an optional disc."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)),
        )
    if sun is not None:
        cx, cy = sun
        r = w // 8
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(250, 220, 140))
    img.save(path)
    return path


@pytest.fixture
def shot_factory(tmp_path: Path):
    """make_shot(dir, name, ...) -> shot directory with the contract satisfied."""
    root = tmp_path / "work"

    def make(
        name: str = "shot_001",
        *,
        start: str = "The character stands in the garden, facing the coop.",
        stop: str = "The character has turned away from the coop, looking left.",
        motion: str = "She turns her head slowly.",
        refs: int = 2,
        plates: int = 1,
        audio: bool = True,
        image_size: tuple[int, int] = (640, 384),
    ) -> Path:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "start.txt").write_text(start, encoding="utf-8")
        (d / "stop.txt").write_text(stop, encoding="utf-8")
        (d / "prompt.txt").write_text(motion, encoding="utf-8")
        if audio:
            (d / "audio.wav").write_bytes(b"RIFF....WAVEfmt ")
        for i in range(refs):
            write_image(d / f"ref_char_{i}.png", size=image_size, sun=(80 + i * 40, 120))
        for i in range(plates):
            write_image(d / f"plate_set_{i}.png", size=image_size, top=(90, 120, 80), bottom=(40, 60, 40))
        return d

    return make


@pytest.fixture
def tar_factory(tmp_path: Path):
    """make_tar(shot_dir) -> .tar.gz containing the shot's files."""

    def make(shot_dir: Path) -> Path:
        in_dir = tmp_path / "in"
        in_dir.mkdir(parents=True, exist_ok=True)
        out = in_dir / f"{shot_dir.name}.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            for item in sorted(shot_dir.iterdir()):
                tar.add(item, arcname=item.name)
        return out

    return make


def make_tar_with_member(path: Path, member_name: str, data: bytes = b"x") -> Path:
    """A tarball containing an arbitrary member name -- for escape tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO(data)
    info = tarfile.TarInfo(name=member_name)
    info.size = len(data)
    with tarfile.open(path, "w:gz") as tar:
        tar.addfile(info, buf)
    return path


def make_tar_with_symlink(path: Path, link_name: str, target: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = tarfile.TarInfo(name=link_name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    with tarfile.open(path, "w:gz") as tar:
        tar.addfile(info)
    return path
