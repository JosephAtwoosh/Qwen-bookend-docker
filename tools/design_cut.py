#!/usr/bin/env python3
"""Design the new cut: 250 shots, lengths set by beat function, cuts on the breath.

The old cut was uniform: 126 of 179 chunks ran 10 s or more, which is a slideshow
rhythm and the condition the video model handles worst. This lays out shots by what
they are for:

    hold       a landscape or a silent physical beat   ~14 s, never split
    narration  Mildred's voice-over over travelling    ~4.5 s, split hard
    dialogue   someone is speaking                     ~8 s

Every internal cut is snapped to a detected silence, so no line straddles two
shots. If a chunk has no silence near an ideal boundary, it gets fewer pieces
rather than a bad cut.

    python bookend-repo/tools/design_cut.py --shots 250

Writes .test-tmp/plan/cut-<n>.json and .test-tmp/plan/shot-list.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from pathlib import Path

WORKSPACE = Path.cwd()
PLAN = WORKSPACE / ".test-tmp" / "plan"
AUDIO = WORKSPACE / ".test-tmp" / "audio"
SHEET = PLAN / "chunk-sheet.json"
SILENCES = PLAN / "silences.json"
FFMPEG = WORKSPACE / "tools" / "ffmpeg" / "ffmpeg-9.0.2-essentials_build" / "bin" / "ffmpeg.exe"

TARGET = {"hold": 14.0, "narration": 4.5, "dialogue": 8.0}  # overridden below
SNAP_WINDOW = 0.9   # how far a cut may move to find a silence (set by --snap)

# Chunks that are voice-over over travelling/arriving, or a silent beat. Everything
# else carries speech in scene. Hand-labelled from reading all 179 dialogues.
NARRATION = {2, 3, 4, 5, 6, 14, 15, 26, 27, 40, 41, 42, 44, 45, 51, 59, 60, 77, 97,
             104, 115, 126, 133, 134, 147, 161, 162, 165, 166, 178, 179}
HOLD = {35, 58, 103, 159}


def detect_silences() -> dict:
    if SILENCES.is_file():
        return json.loads(SILENCES.read_text(encoding="utf-8"))
    out = {}
    for path in sorted(AUDIO.glob("m4a")):
        pass
    for path in sorted(AUDIO.glob("*.m4a")):
        cmd = [str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path),
               "-af", "silencedetect=noise=-35dB:d=0.22", "-f", "null", "-"]
        err = subprocess.run(cmd, capture_output=True, text=True, errors="replace").stderr
        starts = [float(x) for x in __import__("re").findall(r"silence_start:\s*(-?[0-9.]+)", err)]
        ends = [float(x) for x in __import__("re").findall(r"silence_end:\s*([0-9.]+)", err)]
        spans = []
        for i, s in enumerate(starts):
            if i < len(ends) and ends[i] > s:
                spans.append((max(0.0, s), ends[i]))
        out[path.stem] = spans
    SILENCES.write_text(json.dumps(out), encoding="utf-8")
    return out


def classify(chunk: dict) -> str:
    idx = chunk["index"]
    if idx in HOLD:
        return "hold"
    if idx in NARRATION:
        return "narration"
    return "dialogue"


def plan_chunk(chunk: dict, silences: list) -> list[dict]:
    """Split one chunk into legal pieces."""
    kind = classify(chunk)
    duration = float(chunk["duration"])
    target = TARGET[kind]
    pieces = max(1, round(duration / target))
    while pieces > 1:
        bounds = []
        ok = True
        for i in range(1, pieces):
            ideal = duration * i / pieces
            near = [((s + e) / 2) for s, e in silences if abs((s + e) / 2 - ideal) <= SNAP_WINDOW]
            if not near:
                ok = False
                break
            bounds.append(min(near, key=lambda c: abs(c - ideal)))
        if ok:
            bounds = sorted(set(bounds))
            edges = [0.0] + bounds + [duration]
            # Reject a layout with a sliver at either end.
            if min(b - a for a, b in zip(edges, edges[1:])) >= MIN_PIECE:
                return [
                    {"in": round(a, 3), "out": round(b, 3), "duration": round(b - a, 3), "kind": kind}
                    for a, b in zip(edges, edges[1:])
                ]
        pieces -= 1
    return [{"in": 0.0, "out": duration, "duration": duration, "kind": kind}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=int, default=250)
    ap.add_argument("--dialogue", type=float, default=0.0, help="target seconds per dialogue shot")
    ap.add_argument("--snap", type=float, default=0.0, help="max move to find a silence")
    ap.add_argument("--min-piece", type=float, default=2.5)
    args = ap.parse_args()

    global SNAP_WINDOW, MIN_PIECE
    if args.snap > 0:
        SNAP_WINDOW = args.snap
    MIN_PIECE = args.min_piece
    if args.dialogue > 0:
        TARGET["dialogue"] = args.dialogue
    chunks = json.loads(SHEET.read_text(encoding="utf-8"))
    silences = detect_silences()

    # Iterate the narration target so the piece count lands on the requested total.
    lo, hi = 2.6, 9.0
    best = None
    for _ in range(24):
        TARGET["narration"] = (lo + hi) / 2
        shots = []
        for chunk in chunks:
            for piece in plan_chunk(chunk, silences.get(chunk["chunk"], [])):
                shots.append({**piece, "chunk": chunk["chunk"], "index": chunk["index"],
                              "scene_no": chunk["scene_no"], "scene": chunk["scene"],
                              "dialogue": chunk["dialogue"] if piece["kind"] != "hold" else ""})
        if best is None or abs(len(shots) - args.shots) < abs(best[0] - args.shots):
            best = (len(shots), list(shots), TARGET["narration"])
        if len(shots) > args.shots:
            lo = TARGET["narration"]
        else:
            hi = TARGET["narration"]

    count, shots, used_target = best
    for i, shot in enumerate(shots, start=1):
        shot["shot"] = i

    total = sum(s["duration"] for s in shots)
    durs = [s["duration"] for s in shots]
    by_kind = {}
    for s in shots:
        by_kind.setdefault(s["kind"], []).append(s["duration"])

    print(f"narration target {used_target:.2f}s -> {count} shots")
    print(f"total {total:.1f}s ({total/60:.2f} min)  mean {statistics.mean(durs):.2f}s  "
          f"median {statistics.median(durs):.2f}s  min {min(durs):.2f}  max {max(durs):.2f}")
    for kind, values in sorted(by_kind.items()):
        print(f"  {kind:<10} {len(values):>4} shots  mean {statistics.mean(values):>5.2f}s  "
              f"max {max(values):>5.2f}s")

    out = {
        "shot_count": count,
        "total_seconds": round(total, 3),
        "mean": round(statistics.mean(durs), 3),
        "shots": shots,
    }
    dest = PLAN / f"cut-{count}.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
