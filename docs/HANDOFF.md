# Handoff to ltx25-docker

`pack` turns approved bookends into the only thing the render box needs: a small
archive per shot, plus one combined bundle.

```
output/ltx_ready/
├── shot_001_ltx.tar.gz     start.jpg, stop.jpg, prompt.txt, audio.wav, sidecars
├── shot_002_ltx.tar.gz
├── manifest.json           seeds, size, model, style, per-shot file list
└── render_plan.txt         or render.sh, once the FLF2V flags are verified
output/ltx_ready_batch.tar.gz   the lot, for one scp
```

References are deliberately **not** included: they were the input, not the
deliverable, and they are what makes these archives too big to move. Per shot the
archive is a few hundred KB, so a 40-shot batch transfers in seconds.

## Step 1 — verify the FLF2V flags (once, on the render box)

```bash
docker compose run --rm ltx2 python -m ltx_pipelines.distilled --help
```

`repo/scripts/paint_still.py` in ltx25-docker establishes the form

```
--image PATH FRAME STRENGTH guide|replace
```

with `FRAME=0` used for a start frame. What is **not** established anywhere in
that repo is:

- the index convention for the *last* frame (`-1` versus `num_frames-1`), and
- whether two `replace` bindings are accepted in one run.

So this repo will not guess. Until the flags are pasted in, `pack` writes
`render_plan.txt` — command skeletons with `<start args>` placeholders — and no
executable script. A script with invented flags is worse than no script.

## Step 2 — pack with the verified flags

```bash
./scripts/run.sh pack --emit-render-sh \
  --start-args '<verified args for frame 0>' \
  --stop-args  '<verified args for the last frame>'
```

Those strings are written verbatim into `render.sh`, so the script contains
exactly the flags a human checked.

## Step 3 — transfer and render

```bash
scp -P $PORT output/ltx_ready_batch.tar.gz root@<box>:/workspace/
# on the render box, inside ltx25-docker:
tar xzf ltx_ready_batch.tar.gz
mkdir -p state/ltx_ready && mv ltx_ready/* state/ltx_ready/
./state/ltx_ready/render.sh
```

`render.sh` calls `ltx2-run` once per shot against the extracted directory.

## What "done" looks like

A clip whose **frame 0 equals the approved `start.jpg`** and whose **last frame
equals the approved `stop.jpg`**. If either differs, the bookend was accepted but
the render was not conditioned on it — check the frame index first, the strength
second.

## Sizes

Both inputs must be identical in pixel dimensions, and match the target video
size, or LTX will rescale and the frame you approved is not the frame it renders.
`pack` writes the size into `manifest.json`; generate at the size you will render
at (`WIDTH`/`HEIGHT` in `.env`), not at a convenient one.
