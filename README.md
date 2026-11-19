# Qwen-bookend-docker

Batch generation of **start and stop frames** ("bookends") for LTX-2.5 first-last-frame-to-video,
using `Qwen/Qwen-Image-Edit-2509` in its own container.

Two repos, one shared artefact:

```
 Qwen-bookend-docker                       ltx25-docker
 ───────────────────                       ────────────
 shots/in/*.tar.gz                         ltx_ready_batch.tar.gz
   ref_*.png, plate_*.png                    shot_XXX_ltx.tar.gz
   start.txt, stop.txt                         start.jpg, stop.jpg
   prompt.txt, audio.wav                       prompt.txt, audio.wav
        │                                          │
        ├── Qwen-Image-Edit-2509  (~58 GB)         ├── LTX-2.5  (~76 GB)
        │   bookend generation                     │   FLF2V render
        ▼                                          ▼
 start.jpg + stop.jpg  ──── review ──── pack ──── clip.mp4
```

The weights never share an image, a volume or a download. That separation is the point: renting a
GPU to make JPEGs should not cost a 76 GB pull, and rendering video should not cost a 58 GB one.

## Why a separate repo

- **Bandwidth.** One `docker compose run` to make bookends, one to render. Neither pays ingress for
  the other's weights.
- **A style bank.** Before you shut the box down, sweep your references across looks
  (`pixar`, `clay`, `cartoon`, `anime`, `noir`, `realistic`) and keep the results. A later project
  reuses them with no Qwen download at all.
- **Bookends reviewed before video is rendered.** Rejected frames cost seconds of GPU, not a full
  clip. This is the loop the workflow actually wants:

```
prep ──► batch ──► pull review pack ──► review ──► delete rejects ──┐
                        ▲                                          │
                        └────────── batch again (only the rejects) ┘
                                                            pack ──► ltx25-docker
```

## Quickstart

```bash
cp .env.example .env
./scripts/run.sh build
./scripts/run.sh download            # ~58 GB, cache it on a persistent volume

# put shot tarballs in ./shots/in/, project references in ./reference/
./scripts/run.sh refs --write-manifest   # hash the references once per project
./scripts/run.sh doctor              # what this box can do, and why
./scripts/run.sh prep                # resolves tasks and prints the plan; no GPU work
./scripts/run.sh batch               # renders only what is missing
./scripts/run.sh check               # metrics + review/index.html + thumbs
./scripts/run.sh pack --emit-render-sh   # bundle approved shots for ltx25-docker
```

Rejecting a frame is deleting one file and re-running `batch`:

```bash
rm shots/work/shot_007/stop.jpg    # and/or edit shots/work/shot_007/stop.txt
./scripts/run.sh batch             # regenerates stop.jpg only
```

## The pair contract

Two generations per shot, in this order — this is the whole reason the repo exists:

```
start.jpg  =  <selected references>  +  start.txt                 -> Qwen-Image-Edit
stop.jpg   =  <selected references>  +  stop.txt  +  start.jpg    -> Qwen-Image-Edit
```

`batch` therefore runs in two phases in `sequential` mode: render every start, then
**re-resolve** the stops, because a stop cannot be planned before its own frame 0
exists. `start.jpg` is appended last in the image list so the `Image N` numbering of
the references never shifts.

References are *selected*, not dumped — the model is trained for 1–3 inputs:

```
reference/default.txt        project-wide, one filename per line
shots/work/shot_007/use.txt  per shot, wins; '-' excludes an inherited file
```

## The commands

| Command | Does | GPU |
|---|---|---|
| `new` | create a project folder from the shipped template (`project.json`, `cast.csv`, `shots.csv`) | no |
| `plan` | validate a project folder: references, texts, durations, grid, duplicates | no |
| `prep` | extract tarballs, compose prompts, derive seeds, print the plan | no |
| `batch` | render missing images across N GPUs, write sidecars (`--phase start` / `--phase stop`) | yes |
| `check` | SSIM / mean-abs-diff / hue distance per pair, verdicts, review pack | no |
| `style-pack` | style x reference sweep -> reusable `style_bank.tar.gz` | yes |
| `pack` | `ltx_ready/` archives + manifest, and a render script once flags are verified | no |
| `refs` | list the reference pool, show each shot's selection, write/verify `manifest.sha256` | no |
| `doctor` | measured VRAM/RAM, chosen profile, worker count, weight cache, reference drift | no |

## The studio (authoring UI)

The CLI assumes the shot tarballs already exist. [`studio/`](studio/README.md) is a
PHP + SQLite app that authors them: characters, props and sets with reference
images, shots with start/stop/motion text and audio, a live preview of the exact
prompt the model will get, bookend review, and a one-click export of the payload
this repo expects.

It is PHP rather than Python so it can be uploaded to ordinary Apache hosting, and
it reimplements prompt composition in PHP for the live preview. That duplication is
pinned by fixtures generated from `bookends/prompt.py`, checked in CI — the two
cannot drift apart without a red build.

```bash
php -S 127.0.0.1:8765 -t studio      # PHP 8.1+ with pdo_sqlite and gd
```

## Starting a new film

```bash
python -m bookends new ~/films/winter-light --name "Winter Light"
```

That writes a small, plain-text project — no database, no build step:

```
project.json     name, pixel size (64-grid), fps, style
cast.csv         name, kind (character/prop/set), description
shots.csv        shot, scene, duration_s, start_text, stop_text, motion_prompt, references
references/      images named <character>_<view>.png, plus an empty plate per set
shots/           one audio file per shot
```

Then:

```bash
python -m bookends plan --check ~/films/winter-light
```

`plan` is the cheap gate before any GPU time. It fails on missing references, empty
start/stop text, quoted dialogue, duplicate shot ids, unparseable durations and
sizes off the 64 grid, and warns on long shots, negations and over-budget prompts.
Every one of those is a fault that would otherwise be discovered twenty minutes into
a paid batch.

The template's own README carries the writing rules, because that is where a new
author will actually look.

## Determinism, so a re-roll is a decision

Seeds are derived, not random: `sha256(seed_base:shot:tag)`. The same shot always produces the same
image, which means:

- re-running a batch does not silently change approved frames;
- changing one thing (prompt, style, `--force`) is an explicit act;
- every `start.json` / `stop.json` records model id + revision, steps, cfg, size, seed, prompt
  sha256, each reference's sha256, GPU name and wall time.

`check` then reduces review to the exceptions: `stalled` (start ≈ stop, nothing to animate) or
`drift` (lighting/background moved, so the video must invent a path). Both are the failure modes that
cost renders in take 2.

## Hardware

**A 24 GB card is enough for VRAM; system RAM is the real constraint.** Full numbers in
[`docs/VRAM.md`](docs/VRAM.md). Short version:

| Component | bf16 | fp8 |
|---|---|---|
| transformer (5 shards) | 40.9 GB | ~20.4 GB |
| text encoder (Qwen2.5-VL 7B) | 16.6 GB | runs on CPU |

One worker needs ~22 GB VRAM and ~37 GB RAM. Two workers need ~74 GB RAM — so on a 32–48 GB box the
second GPU stays idle, and `WORKERS=0` says so instead of quietly running one worker.

## Status: what is proven, and what is not

| Area | State |
|---|---|
| Shot contract, tarball safety, prompt composition | tested (`pytest`, 69 tests, CPU only) |
| Reference selection + `manifest.sha256` drift detection | tested |
| Sequential pair contract (stop consumes its rendered start) | tested, including the fresh-batch case |
| Task resolution, seed derivation, skip/re-roll | tested |
| Review metrics + review pack | tested |
| Handoff packing + manifest | tested |
| CLI end-to-end with a synthetic engine | tested |
| Docker build | **not yet built** — the diffusers revision is unpinned until verified |
| Real generation throughput | **not yet measured** — `./scripts/bench.sh` fills the table in `docs/VRAM.md` |
| FLF2V CLI flags in ltx25-docker | **not yet verified** — see [`docs/HANDOFF.md`](docs/HANDOFF.md); `pack` writes placeholders rather than guess |

## Layout

```
bookends/            the package (torch imported only inside pipe.py)
├── config.py        env/flags -> validated Config
├── scaffold.py      `new`: copy the shipped template into a project folder
├── planfile.py      `plan`: validate project.json / cast.csv / shots.csv
├── templates/short-film/   the starter project, with the writing rules in its README
├── shot.py          the shot contract; safe tarball extraction
├── prompt.py        prompt composition, styles, the anti-boilerplate rules
├── refs.py          reference selection (use.txt / default.txt) + manifest drift
├── tasks.py         task resolution, deterministic seeds, skip logic
├── hardware.py      measured VRAM/RAM -> profile and worker count
├── pipe.py          FakeEngine (CPU) and QwenEngine (fp8/offload profiles)
├── worker.py        process-per-GPU render loop
├── review.py        SSIM/hue metrics, contact sheet, index.html
├── stylepack.py     style & prop bank
├── pack.py          ltx_ready bundles
└── doctor.py        environment reportdocs/                shot contract, hardware numbers, handoff
scripts/             run.sh, entrypoint.sh, download.sh, bench.sh
tests/               69 tests; no GPU, no torch
```

## Developing without a GPU

The whole pipeline is exercisable on a laptop:

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m bookends batch --fake-pipe --dry-run
```

`--fake-pipe` runs the real code path with a deterministic synthetic engine, which is how the review
and handoff logic is tested. Verified on Windows and in CI (Linux, Python 3.10–3.12).

## Non-goals

- **No ComfyUI.** The Licon MSR LoRA ecosystem lives there; this repo deliberately stays a CLI over
  diffusers, like ltx25-docker stays a CLI over `ltx_pipelines`.
- **No text-to-image.** The model is an editor. With no references it will confidently produce
  something unrelated; that path is a warning, not a feature.
- **No lettering in frame.** Captions belong to assembly, authored from the manifest, never painted.

## Licence

MIT (see `LICENSE`). The model is Apache 2.0, ungated.
