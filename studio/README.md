# Qwen-bookend studio

A PHP + SQLite web app for authoring the input to this repo's CLI: characters,
props and sets with their reference images, shots with `start.txt` / `stop.txt` /
`prompt.txt` and `audio.wav`, a live preview of the exact prompt the model will
receive, review of rendered bookends, and a one-click export of the payload the
rented box expects.

It runs locally on this PC and uploads to ordinary Apache + PHP hosting, because
it uses nothing that is not in a stock PHP build: PDO/SQLite, GD, zlib. No
composer, no framework, no build step.

## Why PHP when the CLI is Python

The CLI stays the authority on the shot contract. The studio reimplements the two
things it needs for a live UI — prompt composition and reference selection — and
that duplication is pinned by golden fixtures generated from the Python code, so
the two cannot drift without CI failing:

```
python tools/make_prompt_fixtures.py     # Python writes the expected strings
php studio/tests/run.php                 # PHP must reproduce them byte for byte
```

## Run it locally

Any PHP 8.1+ with `pdo_sqlite` and `gd`:

```bash
php -S 127.0.0.1:8765 -t studio
```

Then open <http://127.0.0.1:8765>. First visit asks you to set a password.

Data lives in `studio/data/` (SQLite + uploaded media + exports). Point it
somewhere else with an environment variable:

```bash
QB_DATA=/path/to/project-data php -S 127.0.0.1:8765 -t studio
```

## Deploy to the website

1. Upload the **contents of `studio/`** into a directory on the site, e.g.
   `public_html/bookends/`.
2. Make `data/` writable (`chmod 775 data`), or move it above the document root and
   set `QB_DATA` in a small `php` auto_prepend or your host's env settings.
3. Open `/bookends/?r=health` and fix anything red. In particular:

   **"data directory not served as static files"** — the app fetches its own
   `data/` URL over HTTP and reports a failure if the server answers 200. That
   would mean your host ignores the supplied `data/.htaccess` and is serving the
   SQLite database and every client image to anyone who guesses the path. If that
   check is red, move `data/` outside the document root rather than trusting the
   `.htaccess`.

Every page except the login form requires the password. Media is never served as
a static file: images and audio stream through `index.php?r=media`, which checks
the session first — so putting the app online does not publish the references at
guessable URLs.

## What it manages

| Entity | Fields |
|---|---|
| Project | name, pixel size (64-grid), fps, project style, notes |
| Character / prop / set | name, kind, description, notes; images with a role (front, three-quarter, side, back, detail, plate) |
| Shot | slug, `start.txt`, `stop.txt`, `prompt.txt`, duration, audio, ordered reference selection, review state |

The **description** of an asset becomes its reference label verbatim, which is
what the model reads: *"Image 1 shows a small brown terrier with a red collar,
front view."* That is the difference between a good pair and a pair that redraws
the character each time.

Image utilities: **Fit: crop** and **Fit: pad** land an upload on the project's
pixel grid with GD, alpha is detected and shown, and off-grid images are flagged
in the asset grid.

## The export

`Export` writes exactly this, then zips it into one `project.tar.gz`:

```
reference/                  every image used, refs.json, manifest.sha256
shots/in/shot_001.tar.gz    start.txt stop.txt prompt.txt use.txt audio.wav
project.json                what this batch is
```

Images travel **once**, in `reference/`, and each shot names its subset in
`use.txt`, so a 40-shot batch is a few hundred KB of text plus the audio. On the
rented box:

```bash
tar xzf project.tar.gz
./scripts/run.sh prep        # shows the plan, no GPU
./scripts/run.sh batch --phase start
./scripts/run.sh batch --phase stop
```

`bookends prep` reads that layout directly, and `bookends refs --verify` checks
`manifest.sha256` — both are exercised against a real export in the smoke test.

## Review

Upload the rendered `*_start.jpg` / `*_stop.jpg` (filenames are matched
automatically), then the Review screen shows each pair side by side with SSIM,
mean absolute difference and hue distance, and the same `pass` / `stalled` /
`drift` verdicts the CLI uses. The CLI is authoritative; this is a pre-screen.

## Erase everything (testing aid)

Settings → **Danger zone** → *Erase everything*. It deletes every project, asset,
reference image, shot, uploaded bookend frame and export — and, if you untick the
box, the password too, returning the app to its setup screen.

It is deliberately easy to remove once it has served its purpose: `src/erase.php`
lists the five places to delete in its header comment (this file, the require, the
two route cases, the view function and the Settings card).

## Tests

```bash
php studio/tests/run.php        # 84 checks: schema, prompt conformance, tar, export, GD, metrics, erase
node tools/studio-smoke.mjs http://127.0.0.1:8765 .test-tmp/studio-smoke   # live HTTP walk-through
```

## Limitations, stated rather than discovered

- **One password, one user.** No accounts, no roles. Fine for one studio; do not
  hand out the password.
- **No HTTPS of its own.** Put it behind the host's TLS, and never log in over
  plain HTTP on a public network.
- **Review-pack upload is per file.** Drag the JPEGs in; the PHP build is not
  assumed to have the `zip` extension, and the app does not parse `.tar.gz` uploads.
- **No remote control.** The app writes the payload; you move it. Driving a rented
  box over SSH from a public web app is a bigger decision than it looks.
- **The built-in dev server serves `data/`.** `php -S` ignores `.htaccess`, so the
  health check will (correctly) report the exposure locally. Apache does not.
