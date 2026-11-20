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

from . import __version__, hardware, planfile, refs, review, scaffold, stylepack
from .config import PROFILES, Config, ConfigError
from .doctor import collect as doctor_collect
from .doctor import format_text as doctor_text
from .pack import pack as pack_shots
from .pipe import s_per_image
from .prompt import load_labels
from .refs import SelectionError
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
    g.add_argument("--profile", choices=PROFILES)
    g.add_argument(
        "--engine",
        choices=("local", "tokenplan"),
        help="local runs the weights here; tokenplan calls the hosted endpoint (no GPU, bills per image)",
    )
    g.add_argument("--api-model", dest="api_model", help="hosted model id (default qwen-image-3.0-pro)")
    g.add_argument("--api-host", dest="api_host", help="hosted API host")
    g.add_argument("--api-key-file", dest="api_key_file", help="file holding the hosted API key")
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
    batch.add_argument(
        "--phase",
        choices=("all", "start", "stop"),
        default="all",
        help="sequential mode renders starts first, then stops that consume them; "
        "use --phase start to review frame 0 before paying for frame N-1",
    )
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

    refs_cmd = sub.add_parser("refs", help="list references, write or verify the manifest")
    _common(refs_cmd)
    refs_cmd.add_argument("--list", action="store_true", help="list the reference pool")
    refs_cmd.add_argument("--per-shot", dest="per_shot", action="store_true", help="show what each shot selects")
    refs_cmd.add_argument("--write-manifest", dest="write_manifest", action="store_true")
    refs_cmd.add_argument("--verify", action="store_true")

    newp = sub.add_parser("new", help="create a project folder from the shipped template")
    newp.add_argument("path", help="directory to create")
    newp.add_argument("--name", help="project name (defaults to the directory name)")
    newp.add_argument("--width", type=int, default=1280)
    newp.add_argument("--height", type=int, default=768)
    newp.add_argument("--style", default="", help="project style key or free text")
    newp.add_argument("--force", action="store_true", help="write into a non-empty directory")

    planp = sub.add_parser("plan", help="validate a project folder")
    planp.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    planp.add_argument("--max-words", dest="max_words", type=int, help="prompt word budget per frame")
    planp.add_argument("--check", action="store_true", help="validate and report (the default)")
    return p


def config_from(args: argparse.Namespace) -> Config:
    keys = (
        "shots_dir", "reference_dir", "output_dir", "models_dir", "model_id", "revision",
        "width", "height", "steps", "workers", "profile", "pair_mode", "style",
        "seed_base", "jpeg_quality", "max_prompt_words",
        "engine", "api_model", "api_host", "api_key_file",
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

    remaining = args.limit

    def take(items: list) -> list:
        nonlocal remaining
        if remaining is None:
            return items
        taken = items[: max(0, remaining)]
        remaining -= len(taken)
        return taken

    # The stop contract is [selected references, stop.txt, start.jpg]. start.jpg is
    # frame 0 of this very shot, so a stop cannot be planned before its start has
    # been rendered: in sequential mode we resolve, render the starts, then resolve
    # again so the stops pick the new frame up.
    sequential = cfg.pair_mode == "sequential"
    want_start = args.phase in ("all", "start")
    want_stop = args.phase in ("all", "stop")
    phase_tags = tuple(t for t, want in (("start", want_start), ("stop", want_stop)) if want)

    try:
        tasks, notes = resolve(cfg, shots, tags=phase_tags)
    except SelectionError as exc:
        print(f"reference selection error: {exc}", file=sys.stderr)
        return 2

    # A stop is conditioned on its own shot's start.jpg, so asking for the stop
    # phase alone when some are missing is a planning mistake, not something to
    # paper over. With --phase all the starts are rendered first, so no check needed.
    if args.phase == "stop" and sequential:
        missing = [
            t.shot
            for t in tasks
            if t.tag == "stop" and not (t.out.parent / "start.jpg").is_file()
        ]
        if missing:
            print(
                f"--phase stop needs start.jpg for every shot; {len(missing)} missing "
                f"({', '.join(missing[:6])}{', ...' if len(missing) > 6 else ''}). "
                f"Run --phase start first.",
                file=sys.stderr,
            )
            return 2

    if sequential:
        starts = pending([t for t in tasks if t.tag == "start"], args.force) if want_start else []
        stops_planned = pending([t for t in tasks if t.tag == "stop"], args.force) if want_stop else []
    else:
        tags = set()
        if want_start:
            tags.add("start")
        if want_stop:
            tags.add("stop")
        wanted = [t for t in tasks if t.tag in tags]
        starts = pending(render_order(wanted, cfg.pair_mode), args.force)
        stops_planned = []

    for note in notes:
        print(f"  ! {note}")

    pending_total = len(starts) + len(stops_planned)
    print(
        f"{len(tasks)} tasks resolved, {pending_total} to render "
        f"({len(tasks) - pending_total} already on disk)"
    )
    if args.limit is not None and args.limit < pending_total:
        print(f"  limit {args.limit}: stopping after {args.limit}")

    if not pending_total:
        print("nothing to do")
        return 0

    hw = hardware.recommend(cfg)
    # Resolve "auto" here as well, so the reported profile is the one that runs.
    if cfg.profile == "auto":
        cfg.profile = hw["profile"]
    workers = cfg.workers or hw["workers"]
    if args.fake_pipe:
        workers = 1
    if cfg.engine != "local":
        # The profile describes how weights are placed locally; a hosted run has none,
        # and printing one would be a lie in the log.
        print(
            f"engine {cfg.engine}  model {cfg.api_model}  host {cfg.api_host}  "
            f"size {cfg.width}x{cfg.height}"
        )
    else:
        print(f"profile {cfg.profile}  workers {workers}  size {cfg.width}x{cfg.height}  steps {cfg.steps}")
    for reason in hw["reasons"]:
        print(f"  - {reason}")

    if args.dry_run:
        if sequential:
            if starts:
                print("\nphase start")
                _print_tasks(take(starts))
            if stops_planned:
                print("\nphase stop  (resolved after the starts exist; each will also consume start.jpg)")
                _print_tasks(take(stops_planned))
        else:
            _print_tasks(take(starts))
        return 0

    results: list[dict] = []
    if sequential:
        batch = take(starts)
        if batch:
            print(f"\nphase start: {len(batch)} image(s)")
            results += render(batch, cfg, workers=workers, fake=args.fake_pipe)
        if want_stop and remaining != 0:
            # Now that frame 0 exists for each shot, resolve again: the stop
            # contract is [selected references, stop.txt, start.jpg].
            second, more_notes = resolve(cfg, shots, tags=("stop",))
            for note in more_notes:
                print(f"  ! {note}")
            stops = take(pending([t for t in second if t.tag == "stop"], args.force))
            if stops:
                print(f"\nphase stop: {len(stops)} image(s)")
                results += render(stops, cfg, workers=workers, fake=args.fake_pipe)
            tasks = second
    else:
        batch = take(starts)
        print(f"\nphase all: {len(batch)} image(s)")
        results += render(batch, cfg, workers=workers, fake=args.fake_pipe)

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


def cmd_refs(cfg: Config, args) -> int:
    ref_dir = Path(cfg.reference_dir)

    if args.write_manifest:
        path, count = refs.write_manifest(cfg)
        print(f"wrote {path}  ({count} file(s) hashed)")

    problems, checked = refs.verify_manifest(cfg)
    if args.verify or problems:
        if problems:
            print(f"manifest: {len(problems)} problem(s)" + (f" across {checked} file(s)" if checked else ""))
            for p in problems:
                print(f"  x {p}")
            if args.verify:
                return 1
        elif checked:
            print(f"manifest: ok, {checked} file(s) match")

    if args.list or not (args.write_manifest or args.verify or args.per_shot):
        pool = refs.images_in(ref_dir)
        labels = load_labels(ref_dir)
        print(f"\n{len(pool)} image(s) in {ref_dir}")
        for p in pool:
            label = labels.get(p.name, "")
            print(
                f"  {p.name:<32}{p.stat().st_size / 1024:8.0f} KB  "
                f"{refs.sha256_file(p)[:12]}  {label}"
            )
        if pool:
            print(
                f"\nmodel card: 1-3 input images is where Qwen-Image-Edit is strongest "
                f"(reference/default.txt or a shot's use.txt selects them)"
            )

    if args.per_shot:
        shots = _load_shots(cfg, extract=False)
        if not shots:
            print(f"\nno extracted shots in {cfg.work_dir}")
        for shot in shots:
            try:
                selection = refs.select(cfg, shot)
            except SelectionError as exc:
                print(f"  {shot.name}: ERROR {exc}")
                continue
            print(f"\n{shot.name}: {len(selection.images)} image(s)")
            for i, p in enumerate(selection.images, start=1):
                print(f"  Image {i}: {p.name}")
            for note in selection.notes:
                print(f"  ! {note}")
    return 0


def cmd_new(cfg: Config, args) -> int:
    try:
        result = scaffold.new_project(
            args.path,
            args.name,
            width=args.width,
            height=args.height,
            style=args.style or "",
            force=args.force,
        )
    except scaffold.ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"created {result['path']}")
    print(f"  {result['name']}  {result['size'][0]}x{result['size'][1]}")
    print(f"  {', '.join(result['files'])}")
    print()
    print("next:")
    print(f"  1. fill cast.csv and shots.csv")
    print(f"  2. put images in {Path(result['path']) / 'references'} as <name>_<view>.png")
    print(f"  3. python -m bookends plan --check {result['path']}")
    return 0


def cmd_plan(cfg: Config, args) -> int:
    directory = Path(args.path)
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2
    issues = planfile.check(directory, max_words=args.max_words or cfg.max_prompt_words)
    stats = planfile.summarise(issues)
    for issue in issues:
        if issue.level != "summary":
            print(issue)
    print()
    print(
        f"{stats['shots']} shots, {stats['seconds']}s, {stats['cast']} cast, "
        f"{stats['references']} reference image(s)"
    )
    verdict = "ok" if stats["errors"] == 0 else f"{stats['errors']} error(s)"
    print(f"{verdict}, {stats['warnings']} warning(s)")
    return 2 if stats["errors"] else 0


COMMANDS = {
    "new": cmd_new,
    "plan": cmd_plan,
    "prep": cmd_prep,
    "batch": cmd_batch,
    "check": cmd_check,
    "style-pack": cmd_style_pack,
    "pack": cmd_pack,
    "doctor": cmd_doctor,
    "refs": cmd_refs,
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
    except SelectionError as exc:
        print(f"reference selection error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
