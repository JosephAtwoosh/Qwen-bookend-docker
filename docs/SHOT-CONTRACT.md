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

## References

Two sources, merged in a fixed order so `Image N` numbering is stable:

1. `reference/*.png` — project-wide (Tripo character sheets, prop sheets, plates)
2. `shot/ref_*.png` — this shot only
3. `shot/plate_*.png` — the set with nobody in it
4. `start.jpg` — added last, and only for the *stop* task in `sequential` mode

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
| tarball escape attempt | `ShotError`, nothing extracted |
| output size off the 64 grid | configuration error, exit 2, before any GPU work |
