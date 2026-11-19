# A short film

A starting point for a new project, not a finished one. Everything here is plain
text and plain files: no database, no build step, no framework.

```
project.json     name, pixel size, fps, style
cast.csv         who and what is in the film: characters, props, sets
shots.csv        one row per shot: duration, the two frames, the motion
references/      the images those shots name (created empty)
shots/           per-shot audio, one file per shot (created empty)
```

## The loop

1. **Cast.** One row per character, prop or set in `cast.csv`. The `description`
   is the most valuable field in the project: it becomes the label the model reads
   on every generation — *"Image 1 shows a seven-year-old girl, red raincoat, dark
   plaits, muddy wellies, front view."* Vague descriptions produce drifting
   characters.
2. **References.** Put images in `references/`, named `<character>_<view>`, for
   example `girl_front.png`, `girl_side.png`, `girl_three_quarter.png`,
   `gate_plate.png`. A set needs an **empty plate** — the location with nobody in
   it. That single file is what stops the model inventing a background behind your
   character.
3. **Shots.** One row per shot in `shots.csv`:
   - `duration_s` — how long the shot runs
   - `start_text` — what frame 0 shows, 25–45 words
   - `stop_text` — what the last frame shows, one visible change from `start_text`
   - `motion_prompt` — what happens between them, for the video model
   - `references` — semicolon-separated stems from `references/`, in slot order
4. **Check.** `bookends plan --check .` reports missing references, empty texts,
   over-long prompts, duplicated shot numbers and sizes off the 64 grid.
5. **Generate.** Import the project into the studio (`python -m bookends` covers
   the CLI side; `studio/` is the browser UI), or export `reference/` +
   `shots/in/` and run `prep` then `batch` on the rented GPU.

## Rules that are cheaper to follow than to debug

- **One visible change per pair.** A start and stop that differ by a gesture will
  interpolate; a pair that differs by a location will invent a third one.
- **No dialogue in the text.** The words are heard, not painted. Quoted lines end
  up lettered into the frame, misspelled.
- **Explain an absence, don't forbid it.** "The yard is empty" works; "no hens"
  reminds the model that hens are an option.
- **Name only who is in frame.** Naming someone who isn't there invites them in.
- **Short beats cut better than long ones.** Under ~9 s the model interpolates;
  over ~12 s it drifts and invents company.

## Sizes

`project.json` width and height must be multiples of 64 — the video model's grid.
1280×768 is landscape and about 1 MP, where the image model is strongest.
