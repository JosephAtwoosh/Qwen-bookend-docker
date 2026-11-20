#!/usr/bin/env python3
"""Render every remaining bookend, one scene at a time, unattended.

Why per scene rather than one pass over 430 images:

  * a batch runs all starts and then all stops, and the stop phase refuses to run unless
    every shot already has a start. Rendering all 430 starts first would risk a credit
    stop leaving 430 orphans. A scene at a time means every batch ends with complete pairs.
  * quality problems are systemic. Scene 2 showed two (a portrait reference forcing a
    close-up, the cage drifting outdoors). Finding that after one scene costs ~1,600
    credits; finding it after twelve costs ~20,000.

Already-rendered images are skipped, so the script is safe to re-run and safe to interrupt.

  python bookend-repo/tools/produce_scenes.py [--scenes 3-15] [--limit-scenes N]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / ".test-tmp" / "payload-all"
STAGE = ROOT / ".test-tmp" / "produce-stage"
KEY_FILE = r"C:\dsh\dashscope.txt"
PYTHONPATH = os.pathsep.join([str(ROOT / ".pylibs"), str(ROOT / "bookend-repo")])


def parse_scenes(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in spec.split(",")]


def shots_for(scene: int) -> list[Path]:
    return sorted((PAYLOAD / "shots" / "in").glob(f"sc{scene:02d}_*.tar.gz"))


def is_done(tar: Path) -> bool:
    slug = tar.name.replace(".tar.gz", "")
    work = PAYLOAD / "shots" / "work" / slug
    return (work / "start.jpg").is_file() and (work / "stop.jpg").is_file()


def stage(scene: int, tars: list[Path]) -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "shots" / "in").mkdir(parents=True)
    (STAGE / "reference").mkdir(parents=True)
    for tar in tars:
        shutil.copy2(tar, STAGE / "shots" / "in" / tar.name)
    for ref in (PAYLOAD / "reference").glob("*"):
        shutil.copy2(ref, STAGE / "reference" / ref.name)
    return STAGE


def merge_back() -> int:
    work_src = STAGE / "shots" / "work"
    if not work_src.is_dir():
        return 0
    work_dst = PAYLOAD / "shots" / "work"
    work_dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for d in work_src.iterdir():
        if d.is_dir() and any(d.glob("*.jpg")):
            target = work_dst / d.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(d, target)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", default="1-15")
    ap.add_argument("--limit-scenes", type=int, default=0)
    args = ap.parse_args()

    scenes = [s for s in parse_scenes(args.scenes) if shots_for(s)]
    if args.limit_scenes:
        scenes = scenes[: args.limit_scenes]

    started = time.time()
    rendered_total = 0
    for scene in scenes:
        tars = shots_for(scene)
        pending = [t for t in tars if not is_done(t)]
        if not pending:
            print(f"scene {scene:02d}: complete, skipping", flush=True)
            continue
        print(f"\n=== scene {scene:02d}: {len(pending)} of {len(tars)} shots to render", flush=True)
        stage(scene, tars)

        env = dict(os.environ, PYTHONPATH=PYTHONPATH)
        cmd = [
            sys.executable, "-X", "utf8", "-m", "bookends", "batch",
            "--engine", "tokenplan",
            "--api-key-file", KEY_FILE,
            "--api-model", "qwen-image-3.0-pro",
            "--style", "pixar",
            "--width", "1280", "--height", "768",
            "--shots", str(STAGE / "shots"),
            "--reference", str(STAGE / "reference"),
            "--output", str(STAGE / "output"),
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8")
        out = (result.stdout or "") + (result.stderr or "")
        for line in out.splitlines():
            if any(k in line for k in ("rendered", "seconds per image", "FAIL", "error")):
                print("   " + line.strip()[:160], flush=True)

        merged = merge_back()
        rendered_total += merged
        elapsed = (time.time() - started) / 60
        print(f"   merged {merged} shot dirs; {rendered_total} total this run; {elapsed:.0f} min", flush=True)

        if "AllocationQuota" in out:
            print("\nquota exhausted: stopping so the last scene's pairs stay complete", flush=True)
            break
        if "invalid_api_key" in out.lower():
            print("\nkey rejected: stopping", flush=True)
            break

    remaining = sum(1 for t in (PAYLOAD / "shots" / "in").glob("*.tar.gz") if not is_done(t))
    print(f"\ndone: {rendered_total} shot dirs merged, {remaining} shots still to render", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
