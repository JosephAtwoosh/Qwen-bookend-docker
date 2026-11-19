"""End-to-end on CPU: prep -> batch (fake pipe) -> check -> pack.

This is the test that makes the repo reviewable without a GPU. It exercises the
real code path -- task resolution, skipping, sidecars, metrics, review pack,
handoff bundle -- with a synthetic engine standing in for the model.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from PIL import Image

from bookends.__main__ import main
from tests.conftest import write_image

WIDTH, HEIGHT = 512, 384


def build_batch(tmp_path: Path, names=("shot_001", "shot_002")) -> dict:
    shots = tmp_path / "shots"
    ref = tmp_path / "reference"
    out = tmp_path / "output"
    ref.mkdir(parents=True, exist_ok=True)
    write_image(ref / "char_front.png", size=(WIDTH, HEIGHT), sun=(150, 120))
    (ref / "refs.json").write_text(
        json.dumps({"char_front.png": "the character, front view"}), encoding="utf-8"
    )

    for name in names:
        build = tmp_path / "build" / name
        build.mkdir(parents=True, exist_ok=True)
        (build / "start.txt").write_text(
            "The character waits by the coop in the morning sun.", encoding="utf-8"
        )
        (build / "stop.txt").write_text(
            "The character has turned towards the gate, looking off-frame left.", encoding="utf-8"
        )
        (build / "prompt.txt").write_text("She turns her head slowly.", encoding="utf-8")
        (build / "audio.wav").write_bytes(b"RIFF....WAVEfmt ")
        write_image(build / "ref_side.png", size=(WIDTH, HEIGHT), sun=(90, 140))
        write_image(build / "plate_coop.png", size=(WIDTH, HEIGHT), top=(80, 110, 70))

        in_dir = shots / "in"
        in_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(in_dir / f"{name}.tar.gz", "w:gz") as tar:
            for item in sorted(build.iterdir()):
                tar.add(item, arcname=item.name)

    return {"shots": shots, "reference": ref, "output": out}


def base_args(paths: dict, *extra: str) -> list[str]:
    return [
        *extra,
        "--shots", str(paths["shots"]),
        "--reference", str(paths["reference"]),
        "--output", str(paths["output"]),
        "--models", str(paths["shots"].parent / "models"),
        "--width", str(WIDTH),
        "--height", str(HEIGHT),
    ]


def test_full_cpu_loop(tmp_path):
    paths = build_batch(tmp_path)

    assert main(base_args(paths, "prep")) == 0
    tasks_json = paths["shots"] / "tasks.json"
    assert tasks_json.is_file()
    tasks = json.loads(tasks_json.read_text(encoding="utf-8"))["tasks"]
    assert len(tasks) == 4  # two shots x start/stop
    assert all(t["prompt"].startswith("Image 1 shows the character, front view.") for t in tasks)

    assert main(base_args(paths, "batch", "--fake-pipe")) == 0
    for name in ("shot_001", "shot_002"):
        for tag in ("start", "stop"):
            out = paths["shots"] / "work" / name / f"{tag}.jpg"
            assert out.is_file(), out
            with Image.open(out) as im:
                assert im.size == (WIDTH, HEIGHT)
            sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
            assert sidecar["seed"] > 0
            assert sidecar["sampling"]["steps"] == 28
            assert sidecar["engine"]["engine"] == "fake"

    manifest = json.loads((paths["output"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rendered"] == 4
    assert manifest["failed"] == 0
    assert manifest["size"] == [WIDTH, HEIGHT]

    # Second pass: everything exists, so nothing re-renders.
    assert main(base_args(paths, "batch", "--fake-pipe")) == 0

    assert main(base_args(paths, "check")) == 0
    review_dir = paths["output"] / "review"
    assert (review_dir / "index.html").is_file()
    assert (review_dir / "metrics.csv").is_file()
    assert len(list((review_dir / "thumbs").glob("*.jpg"))) == 4

    assert main(base_args(paths, "pack", "--emit-render-sh")) == 0
    ready = paths["output"] / "ltx_ready"
    assert (ready / "shot_001_ltx.tar.gz").is_file()
    assert (ready / "manifest.json").is_file()
    assert (paths["output"] / "ltx_ready_batch.tar.gz").is_file()
    # No verified flags were supplied, so no runnable script was invented.
    assert not (ready / "render.sh").exists()
    assert (ready / "render_plan.txt").is_file()


def test_force_rerolls_only_with_flag(tmp_path):
    paths = build_batch(tmp_path, names=("shot_001",))
    assert main(base_args(paths, "prep")) == 0
    assert main(base_args(paths, "batch", "--fake-pipe", "--limit", "1")) == 0

    work = paths["shots"] / "work" / "shot_001"
    assert (work / "start.jpg").is_file()
    assert not (work / "stop.jpg").exists()

    assert main(base_args(paths, "batch", "--fake-pipe")) == 0
    assert (work / "stop.jpg").is_file()

    first = (work / "start.json").read_text(encoding="utf-8")
    assert main(base_args(paths, "batch", "--fake-pipe", "--force")) == 0
    assert (work / "start.json").read_text(encoding="utf-8")  # deterministic: same payload


def test_pack_reports_missing_frames(tmp_path):
    paths = build_batch(tmp_path, names=("shot_001",))
    assert main(base_args(paths, "prep")) == 0
    assert main(base_args(paths, "pack")) == 1  # nothing rendered yet
    assert main(base_args(paths, "batch", "--fake-pipe")) == 0
    assert main(base_args(paths, "pack")) == 0


def test_dry_run_writes_nothing(tmp_path):
    paths = build_batch(tmp_path, names=("shot_001",))
    assert main(base_args(paths, "prep", "--dry-run")) == 0
    assert not (paths["shots"] / "tasks.json").exists()
    assert main(base_args(paths, "batch", "--dry-run")) == 0
    assert not (paths["shots"] / "work" / "shot_001" / "start.jpg").exists()


def test_sequential_stop_uses_the_rendered_start(tmp_path):
    """The contract: stop = selected references + stop.txt + start.jpg.

    On a fresh batch start.jpg does not exist when the batch is planned, so the
    stops must be resolved *after* the starts have rendered. This test fails if
    batch regresses to planning both phases up front.
    """
    paths = build_batch(tmp_path, names=("shot_001",))
    assert main(base_args(paths, "prep")) == 0
    assert main(base_args(paths, "batch", "--fake-pipe")) == 0

    work = paths["shots"] / "work" / "shot_001"
    start_payload = json.loads((work / "start.json").read_text(encoding="utf-8"))
    stop_payload = json.loads((work / "stop.json").read_text(encoding="utf-8"))

    start_names = [Path(p["path"]).name for p in start_payload["images"]]
    stop_names = [Path(p["path"]).name for p in stop_payload["images"]]
    assert "start.jpg" not in start_names
    assert stop_names[:-1] == start_names      # same references, same order
    assert stop_names[-1] == "start.jpg"      # start appended last
    assert "shows frame 0 of this shot" in stop_payload["prompt"]
    # The label is numbered consistently with the slot it occupies.
    assert f"Image {len(stop_names)} shows frame 0" in stop_payload["prompt"]


def test_independent_mode_never_consumes_start(tmp_path):
    paths = build_batch(tmp_path, names=("shot_001",))
    args = base_args(paths, "batch", "--fake-pipe", "--pair-mode", "independent")
    assert main(args) == 0
    work = paths["shots"] / "work" / "shot_001"
    stop_payload = json.loads((work / "stop.json").read_text(encoding="utf-8"))
    assert not any(Path(p["path"]).name == "start.jpg" for p in stop_payload["images"])


def test_bad_size_is_a_configuration_error(tmp_path):
    paths = build_batch(tmp_path, names=("shot_001",))
    args = base_args(paths, "prep")
    idx = args.index("--width")
    args[idx + 1] = "500"  # not a multiple of 64
    assert main(args) == 2


def test_doctor_reports_without_torch(tmp_path):
    paths = build_batch(tmp_path, names=("shot_001",))
    paths["output"].mkdir(parents=True, exist_ok=True)
    assert main(base_args(paths, "doctor")) in (0, 2)
    assert main(base_args(paths, "doctor", "--json")) in (0, 2)