#!/usr/bin/env python3
"""Assemble the whole film's bookend payload from the beat files.

Sources:
  plan/beats-src/scene-NN.json    the cut: one row per shot, with duration and dialogue
  plan/beats/scene-NN.json        authored beats: start, stop, motion, cast per shot
  plan/beats-scene-01.md          scene 1's beats, which predate the JSON format
  plan/refs-farm/*.png            chickens, composited into the yard
  plan/refs/*.png                 humans, which are page crops with their own setting
  plan/refs/sets/*.png            the set plates

Output: .test-tmp/payload-all/{reference,shots/in}, one tarball per shot, ready for
`bookends prep` and `bookends batch`.

It refuses to build if any shot lacks a beat, if a shot wants more than two references
(a stop spends a slot on its start), or if a reference file is missing -- all three are
cheaper to catch here than on a billed API call.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tarfile
from pathlib import Path

# This script lives in bookend-repo/tools/, but plan/ and .test-tmp/ belong to the
# workspace one level above the repo.
ROOT = Path(__file__).resolve().parents[2]
CUT = ROOT / "plan" / "beats-src"
BEATS = ROOT / "plan" / "beats"
OUT = ROOT / ".test-tmp" / "payload-all"
REFS_FARM = ROOT / "plan" / "refs-farm"
REFS_PLAIN = ROOT / "plan" / "refs"
PLATES = ROOT / "plan" / "refs" / "sets"

MAX_REFS = 2  # a stop adds start.jpg, and the model accepts 3 images

# A wide shot should carry the set, not a character portrait. Portrait references are
# page crops and drag the model into a close-up: scene 2's "wide of the garden" came back
# as Oliver's face. Identity does not matter at that distance, so the plate alone is both
# cheaper and more faithful.
WIDE = ("wide of", "wide view", "high wide", "at a distance", "in the distance", "far edge")

# Character slug -> reference base name. Chickens use the composited (in-yard) versions;
# the humans are page crops that already carry a setting, so matting them fails.
CHICKENS = {
    "frederick": "frederick_three_quarter",
    "mildred": "mildred_three_quarter",
    "hilda": "hilda_front",
    "gerry": "gerry_three_quarter",
    "henry": "henry_three_quarter",
}
HUMANS = {
    "hubbard": "hubbard_front",
    "petra": "petra_front",
    "oliver": "oliver_front",
}
SET = "cherry_tree_farm_winter"

LABELS = {
    "frederick_three_quarter": "the character Frederick, three-quarter view",
    "mildred_three_quarter": "the character Mildred, three-quarter view",
    "hilda_front": "the character Hilda, front view",
    "gerry_three_quarter": "the character Gerry, three-quarter view",
    "henry_three_quarter": "the character Henry, a small pale hen, three-quarter view",
    "hubbard_front": "the character Mrs Hubbard, front view",
    "petra_front": "the character Petra, front view",
    "oliver_front": "the character Oliver, front view",
    SET: "Cherry Tree Farm in winter, the yard empty of characters",
}


def scene1_beats() -> dict:
    """Convert scene 1's markdown table plus the already-rendered payload's use.txt files."""
    text = (ROOT / "plan" / "beats-scene-01.md").read_text(encoding="utf-8")
    work = ROOT / ".test-tmp" / "scene01" / "shots" / "work"
    shots = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        num = int(cells[0])
        slug = f"sc01_{num:03d}"
        use = (work / slug / "use.txt")
        cast = []
        if use.is_file():
            for name in use.read_text(encoding="utf-8").split():
                stem = name.replace(".png", "")
                cast.append("set" if stem.startswith("cherry_tree_farm") else stem.split("_")[0])
        shots.append({
            "index": num,
            "start": cells[3],
            "stop": cells[4],
            "motion": (work / slug / "prompt.txt").read_text(encoding="utf-8").strip()
            if (work / slug / "prompt.txt").is_file() else "",
            "cast": sorted(set(cast)),
        })
    return {"scene": 1, "name": "Muddy Norfolk and Fiery Fred", "shots": shots}


def load_scene(num: int) -> dict:
    if num == 1:
        return scene1_beats()
    path = BEATS / f"scene-{num:02d}.json"
    if not path.is_file():
        raise SystemExit(f"missing beats for scene {num}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def ref_path(base: str) -> Path:
    for candidate in (REFS_FARM / f"{base}.png", REFS_PLAIN / f"{base}.png", PLATES / f"{base}.png"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"missing reference: {base}")


def main() -> int:
    cut = json.loads((ROOT / "plan" / "cut-250.json").read_text(encoding="utf-8"))
    rows = cut if isinstance(cut, list) else cut.get("shots", [])

    # Rows are in film order; group them by scene so beats line up one-for-one.
    by_scene: dict[int, list] = {}
    for row in rows:
        by_scene.setdefault(row["scene_no"], []).append(row)

    # Clear only what this script owns. shots/work holds rendered images and must survive
    # a rebuild -- deleting it once destroyed 70 finished bookends, which were only
    # recoverable because the source payloads happened to still exist.
    for owned in ("shots/in", "reference"):
        target = OUT / owned
        if target.exists():
            shutil.rmtree(target)
    ref_dir = OUT / "reference"
    shot_dir = OUT / "shots" / "in"
    ref_dir.mkdir(parents=True)
    shot_dir.mkdir(parents=True)

    needed: set[str] = set()
    planned = []
    problems = []

    for scene_no, scene_rows in sorted(by_scene.items()):
        beats = load_scene(scene_no)["shots"]
        if len(beats) != len(scene_rows):
            problems.append(
                f"scene {scene_no}: {len(beats)} beats for {len(scene_rows)} cut rows"
            )
            continue
        for row, beat in zip(scene_rows, beats):
            # Scene 1's beats predate the JSON schema and are numbered 1..16 by shot,
            # while the cut numbers rows by audio chunk (11 chunks, 16 rows). They are
            # aligned by order there; every later scene carries the cut's own index.
            if scene_no != 1 and beat["index"] != row["index"]:
                problems.append(
                    f"scene {scene_no}: beat index {beat['index']} vs cut {row['index']}"
                )
            cast = list(dict.fromkeys(beat.get("cast") or []))
            if any(marker in beat["start"].lower() for marker in WIDE) and "set" in cast:
                cast = ["set"]
            if len(cast) > MAX_REFS:
                problems.append(
                    f"scene {scene_no} shot {row['index']}: {len(cast)} references, max {MAX_REFS}"
                )
            bases = []
            for c in cast:
                if c == "set":
                    bases.append(SET)
                elif c in CHICKENS:
                    bases.append(CHICKENS[c])
                elif c in HUMANS:
                    bases.append(HUMANS[c])
                else:
                    problems.append(f"scene {scene_no} shot {row['index']}: unknown cast '{c}'")
            needed.update(bases)
            planned.append({
                "scene": scene_no,
                "index": row["index"],
                "duration": row["duration"],
                "kind": row["kind"],
                "start": beat["start"],
                "stop": beat["stop"],
                "motion": beat["motion"],
                "bases": bases,
            })

    for base in sorted(needed):
        src = ref_path(base)
        shutil.copy2(src, ref_dir / f"{base}.png")
    (ref_dir / "refs.json").write_text(
        json.dumps({f"{b}.png": LABELS.get(b, b.replace("_", " ")) for b in sorted(needed)}, indent=2) + "\n",
        encoding="utf-8",
    )

    # Number shots by position within the scene, not by cut index: 57 chunks are split
    # into two shots, so index alone collides and silently overwrites a tarball.
    counters: dict[int, int] = {}
    for shot in planned:
        counters[shot["scene"]] = counters.get(shot["scene"], 0) + 1
        slug = f"sc{shot['scene']:02d}_{counters[shot['scene']]:03d}"
        files = {
            "start.txt": shot["start"].strip() + "\n",
            "stop.txt": shot["stop"].strip() + "\n",
            "prompt.txt": (shot["motion"] or "The moment passes.").strip() + "\n",
            "use.txt": "\n".join(f"{b}.png" for b in shot["bases"]) + "\n",
        }
        with tarfile.open(shot_dir / f"{slug}.tar.gz", "w:gz") as tar:
            for name, text in files.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    print(f"beats:     {len(planned)} shots")
    print(f"references:{len(needed):>3} files")
    print(f"images:    {len(planned) * 2} (start + stop)")
    print(f"seconds:   {sum(s['duration'] for s in planned):.1f}")
    if problems:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for p in problems[:40]:
            return_problem = None
            print("  " + p, file=sys.stderr)
        return 1
    print("\nno problems: every shot has a beat, <=2 references, all files present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
