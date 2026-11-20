# Beats: how a shot is described

One file per scene, `plan/beats/scene-NN.json`, one entry per **cut row** (250 rows from
179 audio chunks — 57 chunks are split into two shots, so those rows carry an "A" frame
and a "B" continuation).

```json
{"index": 41, "start": "...", "stop": "...", "motion": "...", "cast": ["mildred", "set"]}
```

`index` is the cut's own index, used only to catch misalignment. Durations, kinds and
dialogue come from `plan/beats-src/scene-NN.json`, which is generated from the cut.

## The rules these follow

**A frame, not a paragraph.** 25–45 words. The tool adds the reference preamble, the
style clause and the no-text clause, so the final prompt lands near 90–110 words.

**Restate the constant, change one thing.** The stop differs from the start in a single
visible way. Everything that must survive — who is present, the setting, the light — has
to be said again in the stop, or the model quietly removes it. Learned the hard way:

- naming only Frederick in a stop deleted the three hens behind him
- "his breath clouding in the cold air" turned a muddy December yard into deep snow
- "Hilda's shadow" read as a barn interior, so the shot went indoors

**Name the setting when the references cannot.** A stop has two image slots plus its
start, so a two-character stop has no room for the plate. Put the place in the words.

**At most two references, and they must both be in frame.** A stop appends `start.jpg`,
and the hosted model accepts three images. Three references plus a start is a 400 after
the request is billed.

**Never name who is not there.** A named character with no reference gets invented, and
then two shots of the same character disagree.

**No dialogue, no lettering, no negations.** The words are heard, not painted. Absence is
stated as what is present.

**Character references already carry the world.** Chicken turnarounds are composited into
the winter plate by `tools/relight_refs.py`, so a reference places the bird in the yard
instead of on a studio sweep. Human references are page crops and keep their own setting.

## Checking a build before spending anything

```bash
python bookend-repo/tools/build_beats_payload.py     # builds and validates all 250
bookends prep --dry-run --shots .test-tmp/payload-all/shots \
  --reference .test-tmp/payload-all/reference
```

The builder refuses a shot with no beat, more than two references, or a reference file
that does not exist — all three are cheaper to find here than on a billed call.
