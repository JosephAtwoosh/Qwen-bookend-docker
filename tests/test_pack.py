"""Packing for handoff: only complete shots, no heavy references, honest render plan."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from bookends.config import Config
from bookends.pack import pack
from bookends.shot import load_shot
from tests.conftest import write_image


def make_config(tmp_path: Path) -> Config:
    return Config(
        shots_dir=tmp_path / "shots",
        reference_dir=tmp_path / "reference",
        output_dir=tmp_path / "output",
        models_dir=tmp_path / "models",
        width=1280,
        height=768,
    )


def fill_bookends(shot, *, seed=7, seconds=12.5):
    write_image(shot.root / "start.jpg", size=(1280, 768))
    write_image(shot.root / "stop.jpg", size=(1280, 768), sun=(200, 90))
    for tag in ("start", "stop"):
        (shot.root / f"{tag}.json").write_text(
            json.dumps({"seed": seed, "seconds": seconds, "prompt": f"{tag} prompt"}),
            encoding="utf-8",
        )


def test_strict_pack_skips_incomplete(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    complete = load_shot(shot_factory("shot_001", refs=1, plates=0))
    incomplete = load_shot(shot_factory("shot_002", refs=1, plates=0))
    fill_bookends(complete)

    result = pack(cfg, [complete, incomplete])
    assert result["packed"] == 1
    assert [s["shot"] for s in result["skipped"]] == ["shot_002"]
    assert (tmp_path / "output" / "ltx_ready" / "shot_001_ltx.tar.gz").is_file()
    assert not (tmp_path / "output" / "ltx_ready" / "shot_002_ltx.tar.gz").exists()


def test_allow_incomplete_packs_anyway(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    complete = load_shot(shot_factory("shot_001", refs=1, plates=0))
    partial = load_shot(shot_factory("shot_002", refs=1, plates=0))
    fill_bookends(complete)
    write_image(partial.root / "start.jpg", size=(1280, 768))

    result = pack(cfg, [complete, partial], allow_incomplete=True)
    assert result["packed"] == 2
    with tarfile.open(tmp_path / "output" / "ltx_ready" / "shot_002_ltx.tar.gz") as tar:
        names = tar.getnames()
    assert "start.jpg" in names and "stop.jpg" not in names


def test_archive_carries_no_references(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=2, plates=1))
    fill_bookends(shot)
    pack(cfg, [shot])

    with tarfile.open(tmp_path / "output" / "ltx_ready" / "shot_001_ltx.tar.gz") as tar:
        names = set(tar.getnames())
    assert names == {"start.jpg", "stop.jpg", "prompt.txt", "audio.wav", "start.json", "stop.json"}
    assert not any(n.startswith("ref_") or n.startswith("plate_") for n in names)


def test_manifest_records_seeds_and_size(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    fill_bookends(shot, seed=1234)
    pack(cfg, [shot])

    manifest = json.loads((tmp_path / "output" / "ltx_ready" / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["shots"][0]
    assert entry["seeds"] == {"start": 1234, "stop": 1234}
    assert manifest["size"] == [1280, 768]
    assert entry["has_audio"] is True


def test_combined_bundle_contains_the_ready_tree(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    fill_bookends(shot)
    result = pack(cfg, [shot])

    with tarfile.open(result["bundle"]) as tar:
        names = tar.getnames()
    assert "ltx_ready/manifest.json" in names
    assert "ltx_ready/shot_001_ltx.tar.gz" in names


def test_no_flags_means_no_render_script(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    fill_bookends(shot)
    result = pack(cfg, [shot], emit_render_sh=True)

    ready = tmp_path / "output" / "ltx_ready"
    assert not (ready / "render.sh").exists()
    assert (ready / "render_plan.txt").is_file()
    assert "verify" in (ready / "render_plan.txt").read_text(encoding="utf-8").lower()
    assert "warning" in result


def test_verified_flags_produce_a_render_script(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    fill_bookends(shot)
    result = pack(
        cfg,
        [shot],
        emit_render_sh=True,
        start_args="--image-frame 0 --image-strength 1.0",
        stop_args="--image-frame -1 --image-strength 1.0",
    )
    script = (tmp_path / "output" / "ltx_ready" / "render.sh").read_text(encoding="utf-8")
    assert "--image-frame 0" in script
    assert "shot_001" in script
    assert result["render_sh"].endswith("render.sh")


def test_empty_pack_reports_cleanly(shot_factory, tmp_path):
    cfg = make_config(tmp_path)
    shot = load_shot(shot_factory("shot_001", refs=1, plates=0))
    result = pack(cfg, [shot])
    assert result["packed"] == 0
    assert result["note"] == "nothing complete to pack"
