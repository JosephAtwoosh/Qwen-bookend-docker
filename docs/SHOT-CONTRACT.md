# Shot contract

A *shot* is one directory, delivered as a tarball, describing two frames: where the
shot starts and where it ends. Everything else about the video is LTX's problem.

```
shots/in/shot_001.tar.gz
└── shot_001/
    ├── start.txt      required   what frame 0 shows
    ├── stop.txt       required   what the last frame shows
    ├── prompt.txt     required   the LTX motion prompt (carried, not used here)
    ├── audio.wav      optional   carried through to the handoff
    ├── ref_*.png      optional   shot-specific references
    ├── plate_*.png    optional   empty background plates for the set
    └── refs.json      optional   filename -> description, for the prompt preamble
```

A tarball whose contents are wrapped in one folder (`tar czf shot.tar.gz shot_001/`)
is flattened automatically. A tarball containing absolute paths, `..` segments or
links is **rejected**, not unpacked.

## start.txt and stop.txt describe the change, not the shot

This is the rule that matters. The bookend is an *edit* of the references, so the
text says only what is different about this frame:

```
good:  The character has turned towards the gate, looking off-frame left.
bad:   A photorealistic garden at dawn, 35mm anamorphic, a hen, a character in a
       blue jacket, no dogs, no captions, 24fps, cinematic lighting, ...
```

The tool assembles the rest:

```
<Image 1 shows … Image 2 shows …>  +  <your text>  +  <style>  +  <no-text clause>
```

so nobody hand-writes slot boilerplate per shot. Prompts over `MAX_PROMPT_WORDS`
(default 120) are reported in `prep` and `batch`; quotation marks and negations are
flagged too, because take 2's 505-word prompts with 15.5 negations each were the
documented cause of species-blending and lettered dialogue.

## The pair contract

Two generations per shot, in this order:

```
start.jpg  =  <selected references>  +  start.txt                    ->  Qwen-Image-Edit
stop.jpg   =  <selected references>  +  stop.txt  +  start.jpg       ->  Qwen-Image-Edit
```

`start.jpg` is appended **last** in the stop's image list, so the slot numbering of
the references never shifts, and it is labelled
`"frame 0 of this shot, already approved and rendered"`.

This is why `batch` runs in two phases in `sequential` mode: it renders every
start, then resolves the stops again — because a stop cannot be planned before its
own frame 0 exists on disk. `--pair-mode independent` drops the `start.jpg` input
(and the two-phase ordering) for shots where the camera moves too far between
frames for one to guide the other.

One `batch` run does both passes. To review frame 0 before paying for frame N-1,
run them separately:

```bash
bookends batch --phase start     # every start.jpg
# review, fix prompts, delete rejects
bookends batch --phase stop      # stops, each consuming its own start.jpg
```

`--phase stop` **refuses to run** if any shot is missing its `start.jpg`, naming
the shots. It does not quietly fall back to generating stops without frame 0 — that
fallback is the bug this whole section exists to prevent.

## References

Three sources, in this order, so `Image N` numbering is stable and visible in
`bookends prep`:

1. selected project references from `reference/` — see *Selecting references*
2. `shot/ref_*.png` — this shot only (always included)
3. `shot/plate_*.png` — the set with nobody in it (always included)
4. `start.jpg` — added last, and only for the *stop* task in `sequential` mode

## Selecting references

Qwen-Image-Edit is trained for multi-image editing with 1–3 inputs; more than that
degrades quality rather than adding information. So references are selected, not
dumped:

```
reference/default.txt          project-wide default, one filename per line
shots/work/shot_007/use.txt    per shot, wins over default.txt
```

```
# the cast and the set, in slot order
char_front.png
plate_garden.png
-ref_crowd.png                 # a leading '-' excludes a default-included file
```

Names resolve against the shot directory first, then `reference/`, so a shot can
shadow a shared file. If neither file exists, at most 3 project references are used
and `prep` says so — an implicit choice is reported rather than hidden.

`bookends refs --list` shows what is available with labels and hashes;
`bookends refs --per-shot` prints exactly which images each shot will send.

### Drift

`bookends refs --write-manifest` hashes every file in `reference/` (including
`refs.json`, because labels are an input to every prompt) into
`reference/manifest.sha256`. `bookends refs --verify` — and `doctor` — report
missing, changed and unlisted files.

That matters because a changed reference invalidates every bookend already
approved against it: the sidecars record each input's sha256, so the drift is
provable, but only if you check.

### refs.json

Optional, and the difference between "Image 2 shows the side reference" and
"Image 2 shows the character, side view, blue jacket, no hat":

```json
{
  "char_front.png": "the character, front view",
  "char_side.png": "the character, side view",
  "plate_coop.png": "the garden with the coop, early morning, nobody present"
}
```

Put it in `reference/` for project-wide labels, or inside a shot directory for
shot-specific ones. Shot labels win.

## Why plates are not optional in practice

A plate is the set with nobody in it. Without one, asking an *editor* to place a
character against a studio turntable background and also invent a garden means it
will usually keep the turntable. With one, the instruction becomes plain:

```
Image 1 shows the character, front view. Image 3 shows the garden with the coop.
Place the character from Image 1 in the garden from Image 3, ...
```

`prep` warns when a shot has references but no plate.

## Failures, and when you see them

| Situation | Behaviour |
|---|---|
| `start.txt`, `stop.txt` or `prompt.txt` missing | shot skipped at `prep`, named on screen |
| an empty required file | same: an empty prompt is a bug, not an empty shot |
| no `ref_*` and no `plate_*` | warning: this would be text-only generation |
| `start.txt` == `stop.txt` | warning: the shot has nothing to animate |
| `use.txt` names a file that does not exist | error naming it and listing what is available, exit 2, before any GPU work |
| `reference/` has no `manifest.sha256` | warning from `doctor` and `refs --verify` |
| tarball escape attempt | `ShotError`, nothing extracted |
| output size off the 64 grid | configuration error, exit 2, before any GPU work |
