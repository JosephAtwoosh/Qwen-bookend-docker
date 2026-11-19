"""CLI.

    bookends prep        extract shots, resolve every task, print the plan (no GPU)
    bookends batch       render what is missing
    bookends check       metrics + review pack
    bookends style-pack  reusable style/prop bank from ./reference
    bookends pack        bundle approved shots for ltx25-docker
    bookends doctor      what this box can actually do

Every subcommand reads the same env vars as the compose file, and any flag
overrides them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, hardware, review, stylepack
from .config import Config, ConfigError
from .doctor import collect as doctor_collect
from .doctor import format_text as doctor_text
from .pack import pack as pack_shots
from .pipe import s_per_image
from .shot import ShotError, discover_shots, discover_tarballs, extract_shot, load_shot
from .tasks import pending, render_order, resolve, summarise, write_tasks_json
from .worker import batch_manifest, render


# --------------------------------------------------------------------- args
def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("configuration (overrides env)")
    g.add_argument("--shots", dest="shots_dir", type=Path, help="shots directory (default: ./shots)")
    g.add_argument("--reference", dest="reference_dir", type=Path, help="project references (default: ./reference)")
    g.add_argument("--output", dest="output_dir", type=Path, help="outputs (default: ./output)")
    g.add_argument("--models", dest="models_dir", type=Path, help="weight cache (default: ./models)")
    g.add_argument("--model-id", dest="model_id")
    g.add_argument("--revision", dest="revision")
    g.add_argument("--width", type=int)
    g.add_argument("--height", type=int)
    g.add_argument("--steps", type=int)
    g.add_argument("--workers", type=int)
    g.add_argument("--profile", choices=("auto", "fp8-t2i-cpu", "fp8-offload", "bf16-offload"))
    g.add_argument("--pair-mode", dest="pair_mode", choices=("sequential", "independent"))
    g.add_argument("--style", help="style key (pixar, clay, cartoon, anime, noir, realistic) or free text")
    g.add_argument("--seed-base", dest="seed_base", type=int)
    g.add_argument("--jpeg-quality", dest="jpeg_quality", type=int)
    g.add_argument("--max-prompt-words", dest="max_prompt_words", type=int)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bookends", description="Bookend frames for LTX-2.5 FLF2V")
    p.add_argument("--version", action="version", version=f"bookends {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    prep = sub.add_parser("prep", help="extract shots and resolve tasks; no GPU needed")
    _common(prep)
    prep.add_argument("--force", action="store_true", help="re-extract tarballs that already exist")
    prep.add_argument("--dry-run", action="store_true", help="print the plan without writing tasks.json")

    batch = sub.add_parser("batch", help="render missing bookends")
    _common(batch)
    batch.add_argument("--force", action="store_true", help="re-render even if the output exists")
    batch.add_argument("--limit", type=int, help="stop after N images (a cheap first pass)")
    batch.add_argument("--fake-pipe", dest="fake_pipe", action="store_true", help="synthetic images, no model")
    batch.add_argument("--dry-run", action="store_true", help="show what would run")

    check = sub.add_parser("check", help="pair metrics and review pack")
    _common(check)
    check.add_argument("--thumb-width", dest="thumb_width", type=int, default=review.DEFAULT_THUMB_WIDTH)
    check.add_argument("--ssim-high", dest="ssim_high", type=float, default=review.SSIM_HIGH)
    check.add_argument("--ssim-low", dest="ssim_low", type=float, default=review.SSIM_LOW)

    style = sub.add_parser("style-pack", help="build a reusable style/prop bank from ./reference")
    _common(style)
    style.add_argument("--styles", nargs="+", help="style keys or free text (default: all built-ins)")
    style.add_argument("--prompt", default="", help="extra framing/scene text appended to every style")
    style.add_argument("--seed", type=int, help="fixed seed for the whole sweep")
    style.add_argument("--all-refs", dest="all_refs", action="store_true", help="pass every reference to each generation")
    style.add_argument("--force", action="store_true")
    style.add_argument("--dry-run", action="store_true")

    pack = sub.add_parser("pack", help="bundle approved shots for ltx25-docker")
    _common(pack)
    pack.add_argument("--allow-incomplete", dest="allow_incomplete", action="store_true")
    pack.add_argument("--emit-render-sh", dest="emit_render_sh", action="store_true")
    pack.add_argument("--start-args", dest="start_args", help="verified FLF2V args for frame 0")
    pack.add_argument("--stop-args", dest="stop_args", help="verified FLF2V args for the last frame")

    doc = sub.add_parser("doctor", help="report what this box can do")
    _common(doc)
    doc.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def config_from(args: argparse.Namespace) -> Config:
    keys = (
        "shots_dir", "reference_dir", "output_dir", "models_dir", "model_id", "revision",
        "width", "height", "steps", "workers", "profile", "pair_mode", "style",
        "seed_base", "jpeg_quality", "max_prompt_words",
    )
    overrides = {k: getattr(args, k, None) for k in keys}
    return Config.from_env(**overrides)


# ------------------------------------------------------------------ helpers
def _load_shots(cfg: Config, *, extract: bool = True, force: bool = False) -> list:
    """Extract any tarballs, then load every usable shot; report the unusable ones."""
    if extract:
        for tar in discover_tarballs(cfg.in_dir):
            try:
                extract_shot(tar, cfg.work_dir, force=force)
            except (ShotError, OSError) as exc:
                print(f"  skip {tar.name}: {exc}")
    shots = []
    for root in discover_shots(cfg.work_dir):
        try:
            shots.append(load_shot(root))
        except ShotError as exc:
            print(f"  skip {root.name}: {exc}")
    return shots


def _print_tasks(tasks: list) -> None:
    print(f"{'shot':<26}{'tag':<7}{'seed':>11}{'imgs':>6}{'words':>7}  status")
    for t in tasks:
        status = "exists" if t.out.is_file() else "-"
        print(f"{t.shot:<26}{t.tag:<7}{t.seed:>11}{len(t.images):>6}{len(t.prompt.split()):>7}  {status}")
        for note in t.notes:
            print(f"    ! {note}")


# ------------------------------------------------------------------ commands
def cmd_prep(cfg: Config, args) -> int:
    cfg.ensure_dirs()
    shots = _load_shots(cfg, force=args.force)
    if not shots:
        print(f"no shots found: put tarballs in {cfg.in_dir} (or directories in {cfg.work_dir})")
        return 1
    tasks, notes = resolve(cfg, shots)
    _print_tasks(tasks)
    stats = summarise(tasks)
    print(
        f"\n{stats['tasks']} tasks across {stats['shots']} shots; "
        f"prompt words mean {stats['mean_prompt_words']}, max {stats['max_prompt_words']}; "
        f"{stats['noted']} with notes"
    )
    for note in notes:
        print(f"  ! {note}")
    if not args.dry_run:
        path = write_tasks_json(tasks, cfg.shots_dir / "tasks.json")
        print(f"wrote {path}")
    return 0


def cmd_batch(cfg: Config, args) -> int:
    cfg.ensure_dirs()
    shots = _load_shots(cfg)
    if not shots:
        print(f"no shots found: put tarballs in {cfg.in_dir}")
        return 1

    tasks, notes = resolve(cfg, shots)
    order = render_order(tasks, cfg.pair_mode)
    todo = pending(order, args.force)
    if args.limit:
        todo = todo[: args.limit]

    for note in notes:
        print(f"  ! {note}")
    print(f"{len(tasks)} tasks resolved, {len(todo)} to render ({len(tasks) - len(todo)} already on disk)")

    if not todo:
        print("nothing to do")
        return 0

    hw = hardware.recommend(cfg)
    workers = cfg.workers or hw["workers"]
    if args.fake_pipe:
        workers = 1
    print(f"profile {hw['profile']}  workers {workers}  size {cfg.width}x{cfg.height}  steps {cfg.steps}")
    for reason in hw["reasons"]:
        print(f"  - {reason}")

    if args.dry_run:
        _print_tasks(todo)
        return 0

    results = render(todo, cfg, workers=workers, fake=args.fake_pipe)
    manifest = batch_manifest(cfg, tasks, results, hw=hw, style=cfg.style)
    out = Path(cfg.output_dir) / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("ok")]
    stats = s_per_image([r["seconds"] for r in ok if "seconds" in r])
    print(f"\nrendered {len(ok)}/{len(results)}; wrote {out}")
    if stats.get("n"):
        qualifier = " [synthetic: not indicative]" if args.fake_pipe else ""
        print(
            f"seconds per image: mean {stats['mean_s']}, median {stats['median_s']}, "
            f"range {stats['min_s']}-{stats['max_s']}  "
            f"(~{stats['images_per_hour_1gpu']}/hour on 1 GPU){qualifier}"
        )
    failures = [r for r in results if not r.get("ok")]
    if failures:
        print(f"{len(failures)} failed:")
        for f in failures:
            print(f"  {f['key']}: {f.get('error', '')[:200]}")
        return 1
    return 0


def cmd_check(cfg: Config, args) -> int:
    shots = _load_shots(cfg, extract=False)
    if not shots:
        print(f"no extracted shots in {cfg.work_dir}")
        return 1
    summary = review.build(
        cfg,
        shots,
        thumb_width=args.thumb_width,
        ssim_high=args.ssim_high,
        ssim_low=args.ssim_low,
    )
    print(f"reviewed {summary['shots']} pairs: {summary['verdicts']}")
    print(f"open {Path(summary['review_dir']) / 'index.html'}")
    flagged = [p for p in summary["pairs"] if p["verdict"] != "pass"]
    if flagged:
        print("\nneeds a look:")
        for p in flagged:
            print(f"  {p['shot']:<24}{p['verdict']:<10}{p['reasons'][:120]}")
    return 0


def cmd_style_pack(cfg: Config, args) -> int:
    cfg.ensure_dirs()
    try:
        tasks, notes = stylepack.build_tasks(
            cfg,
            styles=args.styles,
            extra_prompt=args.prompt,
            seed=args.seed,
            all_refs=args.all_refs,
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    for note in notes:
        print(f"  ! {note}")

    todo = pending(tasks, args.force)
    print(f"{len(tasks)} style images resolved, {len(todo)} to render")
    if args.dry_run:
        _print_tasks(todo)
        return 0
    if not todo:
        print("nothing to do")
        return 0

    hw = hardware.recommend(cfg)
    workers = cfg.workers or hw["workers"]
    results = render(todo, cfg, workers=workers, fake=False)
    ok = [r for r in results if r.get("ok")]
    print(f"\nrendered {len(ok)}/{len(results)}")
    if ok:
        bundle = stylepack.archive(Path(cfg.output_dir), name="style_bank")
        print(f"packed {bundle}")
    return 0 if len(ok) == len(results) else 1


def cmd_pack(cfg: Config, args) -> int:
    shots = _load_shots(cfg, extract=False)
    if not shots:
        print(f"no extracted shots in {cfg.work_dir}")
        return 1
    result = pack_shots(
        cfg,
        shots,
        allow_incomplete=args.allow_incomplete,
        start_args=args.start_args,
        stop_args=args.stop_args,
        emit_render_sh=args.emit_render_sh,
    )
    print(f"packed {result['packed']} shot(s) into {result['ready_dir']}")
    if result.get("bundle"):
        print(f"bundle: {result['bundle']}")
    for skip in result.get("skipped", []):
        print(f"  skipped {skip['shot']}: missing {', '.join(skip['missing'])}")
    if result.get("warning"):
        print(f"  ! {result['warning']}")
    if result.get("render_sh"):
        print(f"render script: {result['render_sh']}")
    if result.get("render_plan"):
        print(f"render plan: {result['render_plan']}")
    return 0 if result["packed"] else 1


def cmd_doctor(cfg: Config, args) -> int:
    report = doctor_collect(cfg)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(doctor_text(report))
    return 2 if report["errors"] else 0


COMMANDS = {
    "prep": cmd_prep,
    "batch": cmd_batch,
    "check": cmd_check,
    "style-pack": cmd_style_pack,
    "pack": cmd_pack,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = config_from(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.cmd](cfg, args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
