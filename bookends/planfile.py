"""Read and validate a project folder: project.json, cast.csv, shots.csv.

Validation is deliberately pedantic. Every error here is one that would otherwise be
discovered on a rented GPU, twenty minutes into a batch: an empty stop text, a
reference that was never copied, a shot number used twice, a size off the 64 grid.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

PROJECT_FILE = "project.json"
CAST_FILE = "cast.csv"
SHOTS_FILE = "shots.csv"
REFERENCE_DIR = "references"

SHOT_COLUMNS = ("shot", "scene", "duration_s", "start_text", "stop_text", "motion_prompt", "references")
CAST_COLUMNS = ("name", "kind", "description")
KINDS = ("character", "prop", "set")

MIN_DURATION = 0.5
MAX_DURATION = 30.0


@dataclass
class Issue:
    level: str  # "error" | "warn"
    where: str
    message: str

    def __str__(self) -> str:
        mark = "x" if self.level == "error" else "!"
        return f"  {mark} {self.where}: {self.message}"


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(r) for r in reader]
        return list(reader.fieldnames or []), rows


def load(project_dir: Path | str) -> tuple[dict, list[dict], list[dict]]:
    """Load the three files. Missing files raise FileNotFoundError."""
    project_dir = Path(project_dir)
    project = json.loads((project_dir / PROJECT_FILE).read_text(encoding="utf-8"))
    _, cast = _read_csv(project_dir / CAST_FILE)
    _, shots = _read_csv(project_dir / SHOTS_FILE)
    return project, cast, shots


def reference_index(project_dir: Path) -> dict[str, Path]:
    """Stem -> path for every image in references/."""
    folder = Path(project_dir) / REFERENCE_DIR
    if not folder.is_dir():
        return {}
    return {
        p.stem: p
        for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    }


def check(project_dir: Path | str, max_words: int = 120) -> list[Issue]:
    project_dir = Path(project_dir)
    issues: list[Issue] = []

    for name in (PROJECT_FILE, CAST_FILE, SHOTS_FILE):
        if not (project_dir / name).is_file():
            issues.append(Issue("error", name, "missing"))
    if any(i.level == "error" and i.where.endswith(".json") for i in issues) or not (
        project_dir / SHOTS_FILE
    ).is_file():
        return issues

    try:
        project, cast, shots = load(project_dir)
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        return issues + [Issue("error", PROJECT_FILE, f"unreadable: {exc}")]

    # ---- project header
    width = int(project.get("width", 0) or 0)
    height = int(project.get("height", 0) or 0)
    if width % 64 or height % 64:
        issues.append(Issue("error", PROJECT_FILE, f"{width}x{height} is off the 64 grid"))
    if not str(project.get("name", "")).strip():
        issues.append(Issue("warn", PROJECT_FILE, "no project name"))

    # ---- cast
    if cast and not set(CAST_COLUMNS).issubset(cast[0].keys()):
        issues.append(Issue("error", CAST_FILE, f"needs columns {', '.join(CAST_COLUMNS)}"))
    seen_cast: set[str] = set()
    for i, row in enumerate(cast, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            issues.append(Issue("error", f"{CAST_FILE}:{i}", "empty name"))
            continue
        if name in seen_cast:
            issues.append(Issue("error", f"{CAST_FILE}:{i}", f"duplicate name {name!r}"))
        seen_cast.add(name)
        kind = (row.get("kind") or "").strip()
        if kind not in KINDS:
            issues.append(Issue("warn", f"{CAST_FILE}:{i}", f"kind {kind!r} should be one of {', '.join(KINDS)}"))
        if not (row.get("description") or "").strip():
            issues.append(
                Issue("warn", f"{CAST_FILE}:{i}", f"{name} has no description; its reference label will be generic")
            )

    # ---- shots
    if shots and not set(SHOT_COLUMNS).issubset(shots[0].keys()):
        missing = sorted(set(SHOT_COLUMNS) - set(shots[0].keys()))
        issues.append(Issue("error", SHOTS_FILE, f"missing column(s): {', '.join(missing)}"))

    refs = reference_index(project_dir)
    seen_ids: set[str] = set()
    total = 0.0
    for i, row in enumerate(shots, start=2):
        where = f"{SHOTS_FILE}:{i}"
        shot_id = (row.get("shot") or "").strip()
        if not shot_id:
            issues.append(Issue("error", where, "empty shot id"))
        elif shot_id in seen_ids:
            issues.append(Issue("error", where, f"duplicate shot id {shot_id!r}"))
        seen_ids.add(shot_id)

        try:
            duration = float((row.get("duration_s") or "").strip())
        except ValueError:
            issues.append(Issue("error", where, "duration_s is not a number"))
            duration = 0.0
        if duration and not (MIN_DURATION <= duration <= MAX_DURATION):
            issues.append(Issue("error", where, f"duration {duration}s outside {MIN_DURATION}-{MAX_DURATION}s"))
        if duration > 12:
            issues.append(Issue("warn", where, f"{duration:.1f}s is long; the model tends to drift past ~12s"))
        total += duration

        for column, label in (("start_text", "start"), ("stop_text", "stop")):
            text = (row.get(column) or "").strip()
            if not text:
                issues.append(Issue("error", where, f"{label} text is empty"))
                continue
            words = len(text.split())
            if words > max_words:
                issues.append(Issue("warn", where, f"{label} text is {words} words, over the {max_words} budget"))
            if '"' in text or "\u201c" in text or "\u201d" in text:
                issues.append(Issue("error", where, f"{label} text contains quotes; dialogue gets lettered into the frame"))
            if "no " in text.lower():
                issues.append(Issue("warn", where, f"{label} text contains a negation; describe what is there instead"))

        if not (row.get("motion_prompt") or "").strip():
            issues.append(Issue("warn", where, "no motion_prompt; the video model gets only a stills description"))

        for stem in [s.strip() for s in (row.get("references") or "").split(";") if s.strip()]:
            if stem not in refs:
                issues.append(Issue("error", where, f"reference {stem!r} not found in {REFERENCE_DIR}/"))

    if not shots:
        issues.append(Issue("error", SHOTS_FILE, "no shots"))
    elif refs and not any((row.get("references") or "").strip() for row in shots):
        issues.append(Issue("warn", SHOTS_FILE, "no shot names a reference; every generation would be text-only"))

    if not refs:
        issues.append(Issue("warn", REFERENCE_DIR, "no images yet; names in shots.csv will not resolve"))

    summary = {
        "shots": len(shots),
        "seconds": round(total, 2),
        "cast": len(cast),
        "references": len(refs),
        "errors": sum(1 for i in issues if i.level == "error"),
        "warnings": sum(1 for i in issues if i.level == "warn"),
    }
    return issues + [Issue("summary", "plan", json.dumps(summary))]


def summarise(issues: list[Issue]) -> dict:
    for issue in issues:
        if issue.level == "summary":
            return json.loads(issue.message)
    return {"shots": 0, "seconds": 0.0, "cast": 0, "references": 0, "errors": 0, "warnings": 0}
