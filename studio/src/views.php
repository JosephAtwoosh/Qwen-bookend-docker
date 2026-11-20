<?php
/**
 * Server-rendered views. No template engine: PHP is the template language, and a
 * page is a function that returns HTML. Keeps deployment to a file copy.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

function view_layout(string $title, string $body, array $opts = []): string
{
    $nav = '';
    if (logged_in()) {
        $nav = '<nav class="bar">'
            . '<a class="brand" href="' . e(url()) . '">Qwen-bookend</a>'
            . '<a href="' . e(url()) . '">Projects</a>'
            . '<a href="' . e(url('exports')) . '">Exports</a>'
            . '<a href="' . e(url('settings')) . '">Settings</a>'
            . '<a href="' . e(url('health')) . '">Health</a>'
            . '<form method="post" action="' . e(url('logout')) . '" class="inline">' . csrf_field()
            . '<button class="link">Sign out</button></form>'
            . '</nav>';
    }
    $flashes = '';
    foreach (take_flashes() as $f) {
        $flashes .= '<div class="flash ' . e($f['kind']) . '">' . e($f['message']) . '</div>';
    }
    $sub = isset($opts['sub']) ? '<p class="sub">' . $opts['sub'] . '</p>' : '';
    $scripts = isset($opts['script']) ? '<script src="' . e($opts['script']) . '"></script>' : '';

    return '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        . '<meta name="viewport" content="width=device-width, initial-scale=1">'
        . '<title>' . e($title) . ' &middot; ' . QB_APP . '</title>'
        . '<link rel="stylesheet" href="' . e(base_url() . '/assets/app.css') . '"></head><body>'
        . $nav . '<main>' . $flashes
        . '<header class="page"><h1>' . e($title) . '</h1>' . $sub . '</header>'
        . $body
        . '</main>' . $scripts . '</body></html>';
}

/* ---------------------------------------------------------------- auth pages */

function view_setup(): string
{
    return '<main class="narrow"><h1>Set a password</h1><p class="sub">'
        . 'This app stores client reference material. It will be reachable from the internet once uploaded, '
        . 'so it needs a password before it does anything else. Set it now, and note that anyone with this '
        . 'password can read the project.</p>'
        . '<form method="post" action="' . e(url('setup.post')) . '" class="card">' . csrf_field()
        . '<label>Password<input type="password" name="password" minlength="10" required autofocus></label>'
        . '<label>Confirm<input type="password" name="confirm" minlength="10" required></label>'
        . '<button class="primary">Set password</button></form></main>';
}

function view_login(?string $next): string
{
    return '<main class="narrow"><h1>Sign in</h1>'
        . '<form method="post" action="' . e(url('login.post')) . '" class="card">' . csrf_field()
        . '<input type="hidden" name="next" value="' . e((string) $next) . '">'
        . '<label>Password<input type="password" name="password" required autofocus></label>'
        . '<button class="primary">Sign in</button></form></main>';
}

/* ----------------------------------------------------------------- dashboard */

function view_dashboard(array $projects, array $exports): string
{
    $rows = '';
    foreach ($projects as $p) {
        $rows .= '<tr><td><a href="' . e(url('project', ['id' => $p['id']])) . '"><strong>' . e($p['name']) . '</strong></a>'
            . '<div class="muted">' . (int) $p['width'] . '&times;' . (int) $p['height'] . ' &middot; '
            . (int) $p['asset_count'] . ' assets &middot; ' . (int) $p['shot_count'] . ' shots</div></td>'
            . '<td><a class="btn" href="' . e(url('project', ['id' => $p['id']])) . '">Open</a> '
            . '<a class="btn" href="' . e(url('export', ['project' => $p['id']])) . '">Export</a></td></tr>';
    }
    if ($rows === '') {
        $rows = '<tr><td colspan="2" class="muted">No projects yet.</td></tr>';
    }

    $exRows = '';
    foreach (array_slice($exports, 0, 6) as $x) {
        $when = $x['mtime'] ? gmdate('Y-m-d H:i', $x['mtime']) . ' UTC' : '';
        $exRows .= '<tr><td><code>' . e($x['id']) . '</code><div class="muted">' . e($x['project']) . ' &middot; '
            . $x['shots'] . ' shots &middot; ' . $x['images'] . ' images &middot; ' . human_bytes($x['bundle_bytes']) . '</div></td>'
            . '<td class="muted">' . e($when) . '</td>'
            . '<td><a class="btn" href="' . e(url('download', ['id' => $x['id'], 'path' => 'project.tar.gz'])) . '">project.tar.gz</a></td></tr>';
    }
    if ($exRows === '') {
        $exRows = '<tr><td colspan="3" class="muted">No exports yet.</td></tr>';
    }

    return '<h2>New project</h2>'
        . '<form method="post" action="' . e(url('project.create')) . '" class="card row">' . csrf_field()
        . '<label>Name<input name="name" placeholder="The Little Escape" required></label>'
        . '<label>Width<input type="number" name="width" value="1280" step="64" min="256"></label>'
        . '<label>Height<input type="number" name="height" value="768" step="64" min="256"></label>'
        . '<label>Style<input name="style" placeholder="clay, pixar, or blank"></label>'
        . '<button class="primary">Create</button></form>'
        . '<h2>Projects</h2><table class="grid">' . $rows . '</table>'
        . '<h2>Recent exports</h2><table class="grid">' . $exRows . '</table>';
}

/* ------------------------------------------------------------------- project */

function view_project(array $project, array $assetsByKind, array $shots): string
{
    $pid = (int) $project['id'];
    $tiles = '';
    foreach (ASSET_KINDS as $kind => $label) {
        $items = $assetsByKind[$kind] ?? [];
        $cards = '';
        foreach ($items as $a) {
            $img = '';
            $first = db()->prepare('SELECT * FROM images WHERE asset_id = ? ORDER BY position, id LIMIT 1');
            $first->execute([$a['id']]);
            $i = $first->fetch();
            if ($i !== false) {
                $img = '<img loading="lazy" src="' . e(url('media', ['image' => $i['id'], 'v' => 'thumb'])) . '" alt="">';
            } else {
                $img = '<div class="placeholder">no image</div>';
            }
            $cards .= '<a class="card asset" href="' . e(url('asset', ['id' => $a['id']])) . '">' . $img
                . '<div class="pad"><strong>' . e($a['name']) . '</strong>'
                . '<div class="muted">' . (int) $a['image_count'] . ' image(s)</div></div></a>';
        }
        $tiles .= '<section><h3>' . e($label) . 's</h3><div class="cards">' . $cards
            . '<form method="post" action="' . e(url('asset.create')) . '" class="card add">' . csrf_field()
            . '<input type="hidden" name="project_id" value="' . $pid . '">'
            . '<input type="hidden" name="kind" value="' . e($kind) . '">'
            . '<label>New ' . e(strtolower($label)) . '<input name="name" placeholder="Toby" required></label>'
            . '<button>Add</button></form></div></section>';
    }

    $shotRows = '';
    foreach ($shots as $s) {
        $audio = !empty($s['audio_file']) ? '<span class="ok">wav</span>' : '<span class="muted">none</span>';
        $shotRows .= '<tr><td><a href="' . e(url('shot', ['id' => $s['id']])) . '"><strong>' . e($s['slug']) . '</strong></a></td>'
            . '<td>' . (int) $s['asset_count'] . ' refs</td>'
            . '<td>' . $audio . '</td>'
            . '<td>' . (int) $s['bookend_count'] . '/2 frames</td>'
            . '<td>' . e((string) $s['reviewed']) . '</td>'
            . '<td class="muted">' . Prompts::wordCount((string) $s['start_text']) . ' + '
            . Prompts::wordCount((string) $s['stop_text']) . ' words</td></tr>';
    }
    if ($shotRows === '') {
        $shotRows = '<tr><td colspan="6" class="muted">No shots yet.</td></tr>';
    }

    return '<p class="sub">' . e((string) $project['name']) . ' &middot; ' . (int) $project['width'] . '&times;'
        . (int) $project['height'] . ' &middot; style ' . e(Prompts::styleLabel($project['style'])) . '</p>'
        . '<div class="actions">'
        . '<a class="btn primary" href="' . e(url('export', ['project' => $pid])) . '">Export payload</a> '
        . '<a class="btn" href="' . e(url('review', ['id' => $pid])) . '">Review bookends</a> '
        . '<a class="btn" href="' . e(url('project.edit', ['id' => $pid])) . '">Project settings</a></div>'
        . $tiles
        . '<section><h2>Shots</h2><form method="post" action="' . e(url('shot.create')) . '" class="row">' . csrf_field()
        . '<input type="hidden" name="project_id" value="' . $pid . '">'
        . '<button class="primary">Add shot</button></form>'
        . '<table class="grid"><thead><tr><th>Shot</th><th>Refs</th><th>Audio</th><th>Frames</th><th>Review</th><th>Words</th></tr></thead>'
        . '<tbody>' . $shotRows . '</tbody></table></section>';
}

function view_project_edit(array $project): string
{
    return '<form method="post" action="' . e(url('project.save')) . '" class="card">' . csrf_field()
        . '<input type="hidden" name="id" value="' . (int) $project['id'] . '">'
        . '<label>Name<input name="name" value="' . e((string) $project['name']) . '" required></label>'
        . '<div class="row">'
        . '<label>Width<input type="number" name="width" step="64" min="256" value="' . (int) $project['width'] . '"></label>'
        . '<label>Height<input type="number" name="height" step="64" min="256" value="' . (int) $project['height'] . '"></label>'
        . '<label>FPS<input type="number" name="fps" min="1" value="' . (int) $project['fps'] . '"></label>'
        . '</div>'
        . '<label>Style<input name="style" value="' . e((string) ($project['style'] ?? '')) . '" placeholder="clay, pixar, realism..."></label>'
        . '<label>Notes<textarea name="notes" rows="3">' . e((string) ($project['notes'] ?? '')) . '</textarea></label>'
        . '<button class="primary">Save</button></form>'
        . '<form method="post" action="' . e(url('project.delete')) . '" class="card danger" onsubmit="return confirm(\'Delete this project and all its assets and shots?\')">'
        . csrf_field() . '<input type="hidden" name="id" value="' . (int) $project['id'] . '">'
        . '<button class="warn">Delete project</button></form>';
}

/* --------------------------------------------------------------------- asset */

function view_asset(array $asset, array $images, array $project): string
{
    $cards = '';
    foreach ($images as $img) {
        $label = Prompts::labelFor(
            (string) $asset['kind'],
            (string) $asset['name'],
            $asset['description'] === null ? null : (string) $asset['description'],
            (string) $img['role']
        );
        $roles = '';
        foreach (IMAGE_ROLES as $rk => $rl) {
            $roles .= '<option value="' . e($rk) . '"' . ($img['role'] === $rk ? ' selected' : '') . '>' . e($rl) . '</option>';
        }
        $badge = [];
        if ((int) $img['has_alpha'] === 1) {
            $badge[] = '<span class="ok">alpha</span>';
        }
        if ((int) $img['width'] !== (int) $project['width'] || (int) $img['height'] !== (int) $project['height']) {
            $badge[] = '<span class="warnbadge">off-grid</span>';
        }
        $cards .= '<figure class="imgcard">'
            . '<a href="' . e(url('media', ['image' => $img['id'], 'v' => 'full'])) . '" target="_blank">'
            . '<img loading="lazy" src="' . e(url('media', ['image' => $img['id'], 'v' => 'thumb'])) . '" alt=""></a>'
            . '<figcaption><code>' . e((string) $img['file']) . '</code>'
            . '<div class="muted">' . (int) $img['width'] . '&times;' . (int) $img['height'] . ' &middot; '
            . human_bytes((int) $img['bytes']) . ' ' . implode(' ', $badge) . '</div>'
            . '<div class="muted small">' . e($label) . '</div>'
            . '<form method="post" action="' . e(url('asset.image.role')) . '" class="row tight">' . csrf_field()
            . '<input type="hidden" name="id" value="' . (int) $img['id'] . '">'
            . '<select name="role">' . $roles . '</select><button>Set</button></form>'
            . '<form method="post" action="' . e(url('asset.image.fit')) . '" class="row tight">' . csrf_field()
            . '<input type="hidden" name="id" value="' . (int) $img['id'] . '">'
            . '<button name="mode" value="cover" title="Crop to the project size">Fit: crop</button>'
            . '<button name="mode" value="contain" title="Pad to the project size">Fit: pad</button></form>'
            . '<form method="post" action="' . e(url('asset.image.delete')) . '" onsubmit="return confirm(\'Remove this image?\')">'
            . csrf_field() . '<input type="hidden" name="id" value="' . (int) $img['id'] . '">'
            . '<button class="link warn">remove</button></form>'
            . '</figcaption></figure>';
    }
    if ($cards === '') {
        $cards = '<p class="muted">No images yet. Upload the Tripo renders: front, three-quarter, side, and a back view if the shot turns.</p>';
    }

    $kinds = '';
    foreach (ASSET_KINDS as $k => $label) {
        $kinds .= '<option value="' . e($k) . '"' . ($asset['kind'] === $k ? ' selected' : '') . '>' . e($label) . '</option>';
    }
    $roles = '';
    foreach (IMAGE_ROLES as $rk => $rl) {
        $roles .= '<option value="' . e($rk) . '">' . e($rl) . '</option>';
    }

    return '<p class="sub"><a href="' . e(url('project', ['id' => $project['id']])) . '">' . e((string) $project['name']) . '</a></p>'
        . '<form method="post" action="' . e(url('asset.save')) . '" class="card">' . csrf_field()
        . '<input type="hidden" name="id" value="' . (int) $asset['id'] . '">'
        . '<div class="row"><label>Name<input name="name" value="' . e((string) $asset['name']) . '" required></label>'
        . '<label>Kind<select name="kind">' . $kinds . '</select></label></div>'
        . '<label>Description <span class="muted small">(used verbatim as the reference label: '
        . '&ldquo;a small brown terrier with a red collar&rdquo;)</span>'
        . '<textarea name="description" rows="2">' . e((string) ($asset['description'] ?? '')) . '</textarea></label>'
        . '<label>Notes<textarea name="notes" rows="2">' . e((string) ($asset['notes'] ?? '')) . '</textarea></label>'
        . '<button class="primary">Save</button></form>'
        . '<h2>Reference images</h2><div class="cards">' . $cards . '</div>'
        . '<form method="post" action="' . e(url('asset.image.add')) . '" enctype="multipart/form-data" class="card">' . csrf_field()
        . '<input type="hidden" name="asset_id" value="' . (int) $asset['id'] . '">'
        . '<div class="row"><label>Images<input type="file" name="images[]" accept="image/*" multiple required></label>'
        . '<label>Role<select name="role">' . $roles . '</select></label>'
        . '<button class="primary">Upload</button></div>'
        . '<p class="muted small">Target ' . (int) $project['width'] . '&times;' . (int) $project['height']
        . ' px. Uploads are stored at full size; use Fit: crop / Fit: pad to land them on the grid.</p></form>';
}

/* ---------------------------------------------------------------------- shot */

function view_shot(array $shot, array $project, array $assets, array $selection, array $bookends, array $preview): string
{
    $sid = (int) $shot['id'];
    $pos = array_flip($selection); // asset id => slot index
    $sel = $assets;
    usort($sel, static function (array $x, array $y) use ($pos): int {
        return ($pos[(int) $x['id']] ?? PHP_INT_MAX) <=> ($pos[(int) $y['id']] ?? PHP_INT_MAX);
    });

    $boxes = '';
    foreach ($sel as $a) {
        $checked = isset($pos[(int) $a['id']]);
        $count = count(images_for((int) $a['id']));
        $boxes .= '<label class="pick"><input type="checkbox" name="assets[]" value="' . (int) $a['id'] . '"'
            . ($checked ? ' checked' : '') . '> <span class="pickname">' . e((string) $a['kind']) . ' &middot; <strong>'
            . e((string) $a['name']) . '</strong> <span class="muted">(' . $count . ' img)</span></span>'
            . ' <button type="button" class="mini" data-move="-1" title="Move up">&uarr;</button>'
            . '<button type="button" class="mini" data-move="1" title="Move down">&darr;</button></label>';
    }
    if ($boxes === '') {
        $boxes = '<p class="muted">No assets yet. Add characters, props and sets on the project page.</p>';
    }

    $audio = '';
    if (!empty($shot['audio_file'])) {
        $audio = '<audio controls preload="none" src="' . e(url('media', ['audio' => $sid])) . '"></audio>'
            . '<div class="muted small">' . human_bytes((int) $shot['audio_bytes'])
            . ($shot['audio_seconds'] !== null ? ' &middot; ' . round((float) $shot['audio_seconds'], 2) . ' s' : '')
            . '</div><form method="post" action="' . e(url('shot.audio.clear')) . '" class="inline">' . csrf_field()
            . '<input type="hidden" name="id" value="' . $sid . '"><button class="link warn">remove audio</button></form>';
    } else {
        $audio = '<span class="muted">No audio attached.</span>';
    }

    $frames = '';
    foreach (['start', 'stop'] as $tag) {
        $b = $bookends[$tag] ?? null;
        $frames .= '<figure class="imgcard"><figcaption><strong>' . $tag . '.jpg</strong>';
        if ($b !== null) {
            $frames .= '<a href="' . e(url('media', ['bookend' => $sid, 'tag' => $tag])) . '" target="_blank">'
                . '<img loading="lazy" src="' . e(url('media', ['bookend' => $sid, 'tag' => $tag, 'v' => 'thumb'])) . '" alt=""></a>'
                . '<div class="muted small">' . (int) $b['width'] . '&times;' . (int) $b['height'] . ' &middot; '
                . human_bytes((int) $b['bytes']) . '</div>'
                . '<form method="post" action="' . e(url('shot.bookend.clear')) . '" class="inline">' . csrf_field()
                . '<input type="hidden" name="id" value="' . $sid . '"><input type="hidden" name="tag" value="' . $tag . '">'
                . '<button class="link warn">remove</button></form>';
        } else {
            $frames .= '<div class="placeholder">not uploaded</div>';
        }
        $frames .= '</figcaption></figure>';
    }

    $notes = '';
    foreach ($preview['notes'] as $n) {
        $notes .= '<li>' . e($n) . '</li>';
    }

    return '<p class="sub"><a href="' . e(url('project', ['id' => $project['id']])) . '">' . e((string) $project['name'])
        . '</a> &middot; shot <strong>' . e((string) $shot['slug']) . '</strong></p>'
        . '<div class="split">'
        . '<form method="post" action="' . e(url('shot.save')) . '" class="card" id="shotform">' . csrf_field()
        . '<input type="hidden" name="id" value="' . $sid . '">'
        . '<div class="row"><label>Slug<input name="slug" value="' . e((string) $shot['slug']) . '"></label>'
        . '<label>Duration (s)<input type="number" step="0.1" min="0" name="duration_s" value="' . e((string) $shot['duration_s']) . '"></label></div>'
        . '<label>start.txt &mdash; what frame 0 shows<textarea name="start_text" rows="4" data-preview>' . e((string) $shot['start_text']) . '</textarea></label>'
        . '<label>stop.txt &mdash; what the last frame shows<textarea name="stop_text" rows="4" data-preview>' . e((string) $shot['stop_text']) . '</textarea></label>'
        . '<label>prompt.txt &mdash; motion for LTX<textarea name="motion_prompt" rows="3">' . e((string) $shot['motion_prompt']) . '</textarea></label>'
        . '<fieldset><legend>References for this shot <span class="muted small">(tick, then order with &uarr;&darr;)</span></legend>'
        . '<div class="picks">' . $boxes . '</div></fieldset>'
        . '<div class="row"><label>Review<select name="reviewed">'
        . '<option value="pending"' . ($shot['reviewed'] === 'pending' ? ' selected' : '') . '>pending</option>'
        . '<option value="approved"' . ($shot['reviewed'] === 'approved' ? ' selected' : '') . '>approved</option>'
        . '<option value="rejected"' . ($shot['reviewed'] === 'rejected' ? ' selected' : '') . '>rejected</option>'
        . '</select></label></div>'
        . '<label>Review note<textarea name="review_note" rows="2">' . e((string) ($shot['review_note'] ?? '')) . '</textarea></label>'
        . '<button class="primary">Save shot</button></form>'
        . '<div class="side">'
        . '<section class="card"><h3>Prompts as the model will see them</h3>'
        . '<div id="preview-start" class="promptbox">' . e($preview['start']) . '</div>'
        . '<h4>stop</h4><div id="preview-stop" class="promptbox">' . e($preview['stop']) . '</div>'
        . ($notes !== '' ? '<ul class="notes">' . $notes . '</ul>' : '')
        . '<p class="muted small">' . count($preview['labels']) . ' image slot(s). The stop prompt appends frame 0 last, so the numbering stays put.</p>'
        . '</section>'
        . '<section class="card"><h3>Audio</h3>' . $audio
        . '<form method="post" action="' . e(url('shot.audio')) . '" enctype="multipart/form-data" class="row tight">'
        . csrf_field() . '<input type="hidden" name="id" value="' . $sid . '">'
        . '<input type="file" name="audio" accept="audio/*" required><button>Upload</button></form></section>'
        . '<section class="card"><h3>Bookend frames</h3><div class="frames">' . $frames . '</div>'
        . '<form method="post" action="' . e(url('shot.bookend')) . '" enctype="multipart/form-data" class="row tight">'
        . csrf_field() . '<input type="hidden" name="id" value="' . $sid . '">'
        . '<input type="file" name="frames[]" accept="image/jpeg,image/png" multiple>'
        . '<button>Upload frames</button></form>'
        . '<p class="muted small">Filenames ending <code>_start.jpg</code> / <code>_stop.jpg</code> are matched automatically.</p></section>'
        . '<section class="card danger"><h3>Remove</h3>'
        . '<form method="post" action="' . e(url('shot.delete')) . '" class="inline" onsubmit="return confirm(\'Delete this shot?\')">'
        . csrf_field() . '<input type="hidden" name="id" value="' . $sid . '"><button class="warn">Delete shot</button></form>'
        . '<form method="post" action="' . e(url('shot.duplicate')) . '" class="inline">' . csrf_field()
        . '<input type="hidden" name="id" value="' . $sid . '"><button>Duplicate</button></form></section>'
        . '</div></div>'
        . '<script>window.QB_SHOT = ' . json_encode($sid) . ';</script>';
}

/* -------------------------------------------------------------------- review */

function view_review(array $project, array $rows): string
{
    $body = '';
    foreach ($rows as $r) {
        $shot = $r['shot'];
        $m = $r['metrics'];
        $badgeClass = match ($m['verdict'] ?? 'missing') {
            'pass' => 'ok',
            'stalled' => 'warnbadge',
            'drift', 'unreadable' => 'bad',
            default => 'muted',
        };
        $imgs = '';
        foreach (['start', 'stop'] as $tag) {
            $b = $r['bookends'][$tag] ?? null;
            $imgs .= $b === null
                ? '<div class="placeholder">no ' . $tag . '</div>'
                : '<a href="' . e(url('media', ['bookend' => $shot['id'], 'tag' => $tag])) . '" target="_blank">'
                  . '<img loading="lazy" src="' . e(url('media', ['bookend' => $shot['id'], 'tag' => $tag, 'v' => 'thumb'])) . '" alt=""></a>';
        }
        $metrics = [];
        if (($m['ssim'] ?? null) !== null) {
            $metrics[] = 'SSIM ' . number_format((float) $m['ssim'], 3);
        }
        if (($m['mad'] ?? null) !== null) {
            $metrics[] = 'MAD ' . number_format((float) $m['mad'], 1);
        }
        if (($m['hue'] ?? null) !== null) {
            $metrics[] = 'hue ' . number_format((float) $m['hue'], 3);
        }
        $reasons = '';
        foreach ($m['reasons'] ?? [] as $why) {
            $reasons .= '<li>' . e($why) . '</li>';
        }
        $body .= '<tr><td class="shotcell"><strong>' . e((string) $shot['slug']) . '</strong>'
            . '<div><span class="badge ' . $badgeClass . '">' . e((string) ($m['verdict'] ?? 'missing')) . '</span></div>'
            . '<div class="muted small">' . e(implode(' &middot; ', $metrics)) . '</div>'
            . ($reasons !== '' ? '<ul class="notes">' . $reasons . '</ul>' : '')
            . '</td><td>' . $imgs . '</td>'
            . '<td class="detail"><div class="muted small">start</div><div class="promptbox short">' . e($r['preview']['start']) . '</div>'
            . '<div class="muted small">stop</div><div class="promptbox short">' . e($r['preview']['stop']) . '</div>'
            . '<form method="post" action="' . e(url('review.decide')) . '" class="row tight">' . csrf_field()
            . '<input type="hidden" name="id" value="' . (int) $shot['id'] . '">'
            . '<button name="decision" value="approved" class="primary">Approve</button>'
            . '<button name="decision" value="rejected" class="warn">Reject</button>'
            . '<span class="muted small">now: ' . e((string) $shot['reviewed']) . '</span></form></td></tr>';
    }
    if ($body === '') {
        $body = '<tr><td colspan="3" class="muted">No shots with frames yet. Upload start/stop JPEGs on a shot page.</td></tr>';
    }

    return '<p class="sub"><a href="' . e(url('project', ['id' => $project['id']])) . '">' . e((string) $project['name'])
        . '</a> &middot; verdicts are a pre-screen; <code>bookends check</code> on the box is authoritative.</p>'
        . '<table class="grid review"><thead><tr><th>Shot</th><th>Pair</th><th>Prompts &amp; decision</th></tr></thead>'
        . '<tbody>' . $body . '</tbody></table>';
}

/* -------------------------------------------------------------------- export */

function view_export(array $project, array $shots, array $exportsList, ?array $lastResult, string $style): string
{
    $checks = '';
    foreach ($shots as $s) {
        $checks .= '<label class="pick"><input type="checkbox" name="shots[]" value="' . (int) $s['id'] . '" checked> '
            . '<strong>' . e((string) $s['slug']) . '</strong> <span class="muted">('
            . (int) $s['asset_count'] . ' refs, ' . (!empty($s['audio_file']) ? 'audio' : 'no audio') . ')</span></label>';
    }
    if ($checks === '') {
        $checks = '<p class="muted">No shots to export.</p>';
    }

    $list = '';
    foreach ($exportsList as $x) {
        $list .= '<tr><td><code>' . e($x['id']) . '</code><div class="muted">' . $x['shots'] . ' shots &middot; '
            . $x['images'] . ' images</div></td>'
            . '<td><a class="btn" href="' . e(url('download', ['id' => $x['id'], 'path' => 'project.tar.gz'])) . '">project.tar.gz</a> '
            . '<a class="btn" href="' . e(url('download', ['id' => $x['id'], 'path' => 'reference/manifest.sha256'])) . '">manifest</a></td></tr>';
    }
    if ($list === '') {
        $list = '<tr><td colspan="2" class="muted">No exports yet.</td></tr>';
    }

    $result = '';
    if ($lastResult !== null) {
        $missing = '';
        foreach ($lastResult['missing'] as $m) {
            $missing .= '<li>' . e($m) . '</li>';
        }
        $result = '<div class="flash ok">Exported <code>' . e($lastResult['id']) . '</code>: '
            . $lastResult['images'] . ' images, ' . $lastResult['shots'] . ' shots, '
            . human_bytes((int) $lastResult['bytes']) . '. <a href="'
            . e(url('download', ['id' => $lastResult['id'], 'path' => 'project.tar.gz'])) . '">Download project.tar.gz</a></div>'
            . ($missing !== '' ? '<div class="flash warnbadge"><strong>Worth fixing before you rent a box:</strong><ul>' . $missing . '</ul></div>' : '');
    }

    return $result
        . '<form method="post" action="' . e(url('export.run')) . '" class="card">' . csrf_field()
        . '<input type="hidden" name="project" value="' . (int) $project['id'] . '">'
        . '<label>Style for this batch <span class="muted small">(blank = project default: '
        . e(Prompts::styleLabel($project['style'])) . ')</span>'
        . '<input name="style" value="' . e($style) . '" placeholder="clay"></label>'
        . '<fieldset><legend>Shots to include</legend><div class="picks">' . $checks . '</div></fieldset>'
        . '<button class="primary">Build payload</button></form>'
        . '<h2>What gets written</h2><pre class="code">'
        . "reference/                 every image used, refs.json, manifest.sha256\n"
        . "shots/in/shot_001.tar.gz   start.txt stop.txt prompt.txt use.txt audio.wav\n"
        . "project.json               what this batch is\n"
        . "project.tar.gz             all of the above, one download</pre>"
        . '<p class="muted small">On the rented box: unpack into the workspace, then <code>./scripts/run.sh prep</code> '
        . 'to see the plan, and <code>./scripts/run.sh batch</code> to render.</p>'
        . '<h2>Previous exports</h2><table class="grid">' . $list . '</table>';
}

function view_exports(array $exportsList): string
{
    $list = '';
    foreach ($exportsList as $x) {
        $when = $x['mtime'] ? gmdate('Y-m-d H:i', $x['mtime']) . ' UTC' : '';
        $list .= '<tr><td><code>' . e($x['id']) . '</code><div class="muted">' . e($x['project']) . '</div></td>'
            . '<td>' . $x['shots'] . ' shots</td><td>' . $x['images'] . ' images</td>'
            . '<td>' . human_bytes($x['bundle_bytes']) . '</td><td class="muted">' . e($when) . '</td>'
            . '<td><a class="btn" href="' . e(url('download', ['id' => $x['id'], 'path' => 'project.tar.gz'])) . '">download</a></td></tr>';
    }
    if ($list === '') {
        $list = '<tr><td colspan="6" class="muted">Nothing exported yet.</td></tr>';
    }
    return '<table class="grid"><thead><tr><th>Export</th><th>Shots</th><th>Images</th><th>Bundle</th><th>When</th><th></th></tr></thead><tbody>'
        . $list . '</tbody></table>';
}

/* ------------------------------------------------------- erase (TESTING ONLY) */

function view_erase(array $stats): string
{
    $rows = '';
    foreach ([
        'projects' => 'Projects',
        'assets' => 'Characters, props and sets',
        'images' => 'Reference images (database rows)',
        'shots' => 'Shots',
        'bookends' => 'Uploaded start/stop frames',
        'media_files' => 'Files on disk (uploads, thumbnails)',
        'exports' => 'Built exports',
    ] as $key => $label) {
        $value = $key === 'media_files'
            ? (int) $stats[$key] . ' (' . human_bytes((int) $stats['media_bytes']) . ')'
            : (string) (int) $stats[$key];
        $rows .= '<tr><td>' . e($label) . '</td><td><strong>' . e($value) . '</strong></td></tr>';
    }

    return '<p class="sub">Everything below is deleted permanently. There is no undo and no backup '
        . 'taken by the app. The directory guards and this page stay; the database is emptied.</p>'
        . '<table class="grid">' . $rows . '</table>'
        . '<form method="post" action="' . e(url('erase.confirm')) . '" class="card danger">' . csrf_field()
        . '<label>Type <code>ERASE</code> to confirm<input name="confirm" value="" autocomplete="off" required></label>'
        . '<label class="inline-check"><input type="checkbox" name="keep_password" value="1" checked> '
        . 'Keep my password (otherwise you are signed out and the setup screen returns)</label>'
        . '<button class="warn">Erase everything</button></form>';
}

/* ------------------------------------------------------------------ settings */

function view_settings(array $checks, string $width, string $height): string
{
    $rows = '';
    foreach ($checks as $c) {
        $rows .= '<tr><td>' . e($c['label']) . '</td><td>'
            . ($c['ok'] ? '<span class="ok">ok</span>' : '<span class="bad">' . e($c['detail']) . '</span>')
            . '</td></tr>';
    }
    return '<h2>Deployment checks</h2><table class="grid">' . $rows . '</table>'
        . '<p class="muted small">A failure here means the app is reachable in a way it should not be, '
        . 'or cannot write where it needs to. Fix before putting real material in it.</p>'
        . '<h2>Password</h2>'
        . '<form method="post" action="' . e(url('settings.password')) . '" class="card">' . csrf_field()
        . '<label>Current password<input type="password" name="current" required></label>'
        . '<label>New password<input type="password" name="password" minlength="10" required></label>'
        . '<button class="primary">Change password</button></form>'
        . '<h2>Appearance of the preview</h2>'
        . '<form method="post" action="' . e(url('settings.preview')) . '" class="card">' . csrf_field()
        . '<div class="row"><label>Preview width<input name="preview_width" value="' . e($width) . '"></label>'
        . '<label>Preview height<input name="preview_height" value="' . e($height) . '"></label></div>'
        . '<p class="muted small">Only used to show sizes in the preview; the project size is what is exported.</p>'
        . '<button>Save</button></form>'
        // TESTING ONLY: delete this block to remove the erase feature.
        . '<h2>Danger zone</h2>'
        . '<div class="card danger"><p><strong>Erase everything.</strong> Removes every project, '
        . 'reference image, shot, uploaded frame and export. Built for testing this app from a clean '
        . 'slate; it does not take a backup.</p>'
        . '<a class="btn" href="' . e(url('erase')) . '">Erase everything&hellip;</a></div>';
}

/* -------------------------------------------------------------------- health */

function view_health(array $checks, array $env): string
{
    $rows = '';
    foreach ($checks as $c) {
        $rows .= '<tr><td>' . e($c['label']) . '</td><td>'
            . ($c['ok'] ? '<span class="ok">ok</span>' : '<span class="bad">' . e($c['detail']) . '</span>')
            . '</td></tr>';
    }
    $envRows = '';
    foreach ($env as $k => $v) {
        $envRows .= '<tr><td><code>' . e($k) . '</code></td><td>' . e($v) . '</td></tr>';
    }
    return '<p class="sub">What this deployment can and cannot do. Run it again after uploading to a new host.</p>'
        . '<table class="grid">' . $rows . '</table>'
        . '<h2>Environment</h2><table class="grid">' . $envRows . '</table>';
}
