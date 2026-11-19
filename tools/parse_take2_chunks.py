#!/usr/bin/env python3
"""Turn take-2's 179 per-shot prompts into a chunk sheet: scene, cast, dialogue, duration.

The old prompts are not usable as prompts -- they are 2,000-character blocks of
style boilerplate that invite the model to paint everything at once. But they do
contain, per chunk, the one thing a new cut needs as raw material: which scene it
belongs to, who speaks, and the dialogue itself.

Output: .test-tmp/plan/chunk-sheet.json and .test-tmp/plan/chunk-sheet.csv

Run from the workspace that holds shots.json (the take-2 manifest):

    python bookend-repo/tools/parse_take2_chunks.py
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

# The take-2 manifest lives in the workspace, not in this repo, so resolve from the
# working directory rather than from the script location.
WORKSPACE = Path.cwd()
SHOTS = WORKSPACE / "shots.json"
OUT = WORKSPACE / ".test-tmp" / "plan"
DURATIONS = WORKSPACE / ".test-tmp" / "audio-durations.json"

SCENE_RE = re.compile(r"^\s*Scene\s+(\d+)\s*,\s*([^.]+)\.", re.I)
DIALOGUE_RE = re.compile(
    r"dialogue[^:]*:\s*[\"“](.+?)[\"”]",
    re.I | re.S,
)
SPEAKER_RE = re.compile(r"\b([A-Z][a-z]{2,})\s+is\s+the\s+only\s+speaker\b")
QUOTED_ANY = re.compile(r"[\"“]([^\"”]{8,})[\"”]")

# Words that look like names but are not.
NOT_NAMES = {
    "Scene", "Setting", "Locked", "Preserve", "Use", "Give", "Native", "Keep", "End", "Hold",
    "Feet", "Tail", "December", "English", "British", "Fred", "The", "A", "An", "No",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse(shot: dict) -> dict:
    prompt = shot.get("prompt", "") or ""
    scene_no, scene_title = None, None
    m = SCENE_RE.match(prompt)
    if m:
        scene_no = int(m.group(1))
        scene_title = clean(m.group(2))

    dialogue = ""
    d = DIALOGUE_RE.search(prompt)
    if d:
        dialogue = clean(d.group(1))
    else:
        q = QUOTED_ANY.search(prompt)
        if q:
            dialogue = clean(q.group(1))

    speaker = ""
    s = SPEAKER_RE.search(prompt)
    if s:
        speaker = s.group(1)

    return {
        "chunk": shot.get("folder") or f"shot{shot.get('id'):03d}",
        "index": int(shot.get("id", 0)),
        "duration": float(shot.get("duration", 0)),
        "scene_no": scene_no,
        "scene": scene_title or "",
        "speaker": speaker,
        "dialogue": dialogue,
        "prompt_chars": len(prompt),
    }


def main() -> int:
    data = json.loads(SHOTS.read_text(encoding="utf-8"))
    shots = data["shots"]
    durations = json.loads(DURATIONS.read_text(encoding="utf-8")) if DURATIONS.is_file() else {}

    rows = []
    for shot in shots:
        row = parse(shot)
        # Prefer the measured audio duration: the manifest carries the video length.
        measured = durations.get(row["chunk"])
        if measured:
            row["audio_seconds"] = round(float(measured), 3)
        row["duration"] = row.get("audio_seconds", row["duration"])
        rows.append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "chunk-sheet.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    with open(OUT / "chunk-sheet.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = sum(r["duration"] for r in rows)
    scenes = {}
    for r in rows:
        key = (r["scene_no"], r["scene"])
        scenes.setdefault(key, []).append(r)

    print(f"chunks {len(rows)}   total {total/60:.2f} min   mean {statistics.mean(r['duration'] for r in rows):.2f}s")
    print(f"scenes {len(scenes)}   with dialogue {sum(1 for r in rows if r['dialogue'])}"
          f"   without {sum(1 for r in rows if not r['dialogue'])}")
    print("\nscene                                    chunks   seconds")
    for (no, title), items in sorted(scenes.items(), key=lambda kv: (kv[0][0] or 999)):
        print(f"  {no:>2}. {title[:34]:<34} {len(items):>5}   {sum(i['duration'] for i in items):>7.1f}")

    # Who appears: names that show up mid-sentence as speakers or in the action.
    names = Counter()
    for shot in shots:
        for word in re.findall(r"\b([A-Z][a-z]{2,})\b", shot.get("prompt", "")):
            if word not in NOT_NAMES:
                names[word] += 1
    print("\nmost frequent capitalised words (cast candidates):")
    print("  " + ", ".join(f"{w}({n})" for w, n in names.most_common(18)))

    print(f"\nwrote {OUT / 'chunk-sheet.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
