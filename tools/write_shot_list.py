#!/usr/bin/env python3
"""Render the cut plan as a readable shot list, one section per scene.

Adds nothing creative: it is the same JSON the studio will import, laid out so a
human can read it and disagree with the pacing before any frame is written.

    python bookend-repo/tools/write_shot_list.py --cut .test-tmp/plan/cut-250.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

WORKSPACE = Path.cwd()
PLAN = WORKSPACE / ".test-tmp" / "plan"


def clean_scene_titles(shots: list[dict]) -> dict:
    """A few old prompts prefix the title badly; take the commonest title per scene."""
    per_scene = {}
    for shot in shots:
        per_scene.setdefault(shot["scene_no"], []).append(shot["scene"])
    titles = {}
    for no, titles_seen in per_scene.items():
        tally = Counter(t for t in titles_seen if t and not t.lower().startswith("shot "))
        titles[no] = tally.most_common(1)[0][0] if tally else (titles_seen[0] or "Scene")
    return titles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default=str(PLAN / "cut-250.json"))
    args = ap.parse_args()

    data = json.loads(Path(args.cut).read_text(encoding="utf-8"))
    shots = data["shots"]
    titles = clean_scene_titles(shots)

    lines = [
        "# The Little Escape - cut plan",
        "",
        f"{data['shot_count']} shots, {data['total_seconds']:.1f} s "
        f"({data['total_seconds']/60:.2f} min), mean {data['mean']:.2f} s per shot.",
        "",
        "Cuts land on a detected silence, never mid-line. Each row is one render:",
        "`in`/`out` are offsets into the original 179 audio chunks, so the slices",
        "concatenate back to the source with nothing lost.",
        "",
        "Kind: **hold** = silent/landscape, **narration** = voice-over, **dialogue** = in scene.",
        "",
    ]

    current = None
    for shot in shots:
        if shot["scene_no"] != current:
            current = shot["scene_no"]
            scene_shots = [s for s in shots if s["scene_no"] == current]
            seconds = sum(s["duration"] for s in scene_shots)
            lines += [
                "",
                f"## Scene {current}: {titles.get(current, 'Scene')}",
                "",
                f"*{len(scene_shots)} shots, {seconds:.1f} s*",
                "",
                "| # | dur | kind | from | in-out | line |",
                "|--:|----:|------|------|--------|------|",
            ]
        line = (shot["dialogue"] or "").replace("|", "/").strip()
        if len(line) > 96:
            line = line[:96].rstrip() + "..."
        lines.append(
            f"| {shot['shot']} | {shot['duration']:.1f}s | {shot['kind'][:4]} | "
            f"{shot['chunk']} | {shot['in']:.1f}-{shot['out']:.1f} | {line} |"
        )

    dest = PLAN / "shot-list.md"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {dest}  ({len(shots)} shots, {len(titles)} scenes)")

    # A quick read on pacing, per scene, because that is what a cut is judged on.
    print("\nscene   shots  seconds  mean")
    for no in sorted(titles):
        scene_shots = [s for s in shots if s["scene_no"] == no]
        sec = sum(s["duration"] for s in scene_shots)
        print(f"  {no:>2}     {len(scene_shots):>3}   {sec:>7.1f}  {sec/len(scene_shots):>5.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
