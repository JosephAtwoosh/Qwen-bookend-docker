"""Scaffold a new project from the shipped template.

The template is a folder of plain text shipped inside the package, so `bookends new`
works the same from a checkout and from a wheel. Nothing film-specific lives in it:
the point is that the next project starts from the rules, not from this one.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates" / "short-film"
PLACEHOLDER = "{{PROJECT_NAME}}"


class ScaffoldError(Exception):
    """The destination is not usable."""


def template_path() -> Path:
    return TEMPLATE_DIR


def new_project(
    dest: Path | str,
    name: str | None = None,
    *,
    width: int = 1280,
    height: int = 768,
    style: str = "",
    force: bool = False,
) -> dict:
    """Copy the template into `dest` and fill in the project header."""
    dest = Path(dest)
    if not TEMPLATE_DIR.is_dir():
        raise ScaffoldError(f"template missing from the package: {TEMPLATE_DIR}")

    if dest.exists() and any(dest.iterdir()) and not force:
        raise ScaffoldError(
            f"{dest} is not empty; pass --force to write into it anyway"
        )
    if width % 64 or height % 64:
        raise ScaffoldError(
            f"width and height must be multiples of 64 (the video model's grid), "
            f"got {width}x{height}"
        )

    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for item in sorted(TEMPLATE_DIR.iterdir()):
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
        written.append(item.name)

    # project.json carries the placeholders; rewrite rather than string-patch a file.
    project_file = dest / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project["name"] = (name or dest.name or "Untitled film").strip()
    project["width"] = width
    project["height"] = height
    project["fps"] = project.get("fps", 24)
    if style:
        project["style"] = style
    project_file.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")

    readme = dest / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8").replace(PLACEHOLDER, project["name"])
        readme.write_text(text, encoding="utf-8")

    for empty in ("references", "shots"):
        (dest / empty).mkdir(exist_ok=True)

    return {
        "path": str(dest),
        "name": project["name"],
        "size": [project["width"], project["height"]],
        "files": written,
    }
