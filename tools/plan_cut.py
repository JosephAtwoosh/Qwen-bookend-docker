#!/usr/bin/env python3
"""Find legal cut points in the take-2 audio, then plan a new cut.

A cut is legal at a silence, never inside a breath or a word. ffmpeg's silencedetect
gives the silences; this picks cut points from them and lays out a shot list whose
slices concatenate back to exactly the original audio (no gaps, no overlaps).

    python bookend-repo/tools/plan_cut.py --shots 200

Writes .test-tmp/plan/cut-plan.json and prints the shape of the result.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
from pathlib import Path

WORKSPACE = Path.cwd()
AUDIO = WORKSPACE / ".test-tmp" / "audio"
OUT = WORKSPACE / ".test-tmp" / "plan"
SHEET = OUT / "chunk-sheet.json"
FFMPEG = WORKSPACE / "tools" / "ffmpeg" / "ffmpeg-9.0.2-essentials_build" / "bin" / "ffmpeg.exe"

SILENCE_RE = re.compile(r"silence_(start|end):\s*([0-9.]+)")


def silences(path: Path, noise_db: int = -35, min_len: float = 0.25) -> list[tuple[float, float]]:
    """Return (start, end) of detected silences in seconds."""
    cmd = [
        str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_len}",
        "-f", "null", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, errors="replace").stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", out)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is not None and e > s:
            spans.append((max(0.0, s), e))
    return spans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=int, default=200, help="target number of shots")
    ap.add_argument("--min-shot", type=float, default=3.0, help="never plan a shot shorter than this")
    args = ap.parse_args()

    rows = json.loads(SHEET.read_text(encoding="utf-8"))
    plan = []
    for i, row in enumerate(rows):
        if i == 0:
            print("mapping silences for", len(rows), "chunks...")
        path = AUDIO / (row["chunk"] + ".m4a")
        spans = silences(path) if path.is_file() else []
        row["silences"] = [[round(s, 3), round(e, 3)] for s, e in spans]
        # Legal cut points: the middle of each silence long enough to hide a cut.
        row["cut_points"] = [
            round((s + e) / 2, 3)
            for s, e in spans
            if (e - s) >= 0.25
        ]
        plan.append(row)

    total = sum(r["duration"] for r in plan)
    need = max(0, args.shots - len(plan))
    print(f"chunks {len(plan)}  total {total:.1f}s  target {args.shots}  need {need} extra cut(s)")

    # Split the longest chunks first, at the silence nearest their middle.
    order = sorted(plan, key=lambda r: -r["duration"])
    splits = 0
    for row in order:
        if splits >= need:
            break
        if row["duration"] < args.min_shot * 2:
            continue
        candidates = [c for c in row["cut_points"] if args.min_shot <= c <= row["duration"] - args.min_shot]
        if not candidates:
            continue
        mid = row["duration"] / 2
        cut = min(candidates, key=lambda c: abs(c - mid))
        row["split_at"] = cut
        splits += 1
    print(f"splits placed: {splits}  (chunks with no legal cut: {need - splits})")

    # Expand to a shot list.
    shots = []
    for row in plan:
        bounds = [0.0] + ([row["split_at"]] if "split_at" in row else []) + [row["duration"]]
        for a, b in zip(bounds, bounds[1:]):
            shots.append({
                "chunk": row["chunk"],
                "scene_no": row["scene_no"],
                "scene": row["scene"],
                "in": round(a, 3),
                "out": round(b, 3),
                "duration": round(b - a, 3),
                "dialogue": row["dialogue"] if len(bounds) == 2 else "",
            })

    durs = [s["duration"] for s in shots]
    print(f"\nshots {len(shots)}   total {sum(durs):.2f}s ({sum(durs)/60:.2f} min)")
    print(f"min {min(durs):.2f}  median {statistics.median(durs):.2f}  mean {statistics.mean(durs):.2f}  max {max(durs):.2f}")
    buckets = {"<4s": 0, "4-6s": 0, "6-8s": 0, "8-10s": 0, "10-12s": 0, ">12s": 0}
    for d in durs:
        k = "<4s" if d < 4 else "4-6s" if d < 6 else "6-8s" if d < 8 else "8-10s" if d < 10 else "10-12s" if d < 12 else ">12s"
        buckets[k] += 1
    print("distribution:", buckets)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cut-plan.json").write_text(
        json.dumps({"shot_count": len(shots), "total_seconds": round(sum(durs), 3), "shots": shots}, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT / 'cut-plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
