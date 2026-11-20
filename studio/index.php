<?php
/**
 * Qwen-bookend studio -- front controller.
 *
 * Query-string routing on purpose: no mod_rewrite, no PATH_INFO, nothing to
 * configure. It works on Apache, on the PHP built-in server, and on a host that
 * has never heard of pretty URLs.
 */
declare(strict_types=1);

define('QB_BOOT', true);

require __DIR__ . '/src/core.php';
require __DIR__ . '/src/prompts.php';
require __DIR__ . '/src/domain.php';
require __DIR__ . '/src/images.php';
require __DIR__ . '/src/review.php';
require __DIR__ . '/src/export.php';
require __DIR__ . '/src/health.php';
require __DIR__ . '/src/erase.php'; // TESTING ONLY -- see src/erase.php
require __DIR__ . '/src/views.php';

session_start_safe();

$route = isset($_GET['r']) ? (string) $_GET['r'] : '';

/* ------------------------------------------------------------------ helpers */

/** Labels for every image the shot selects, in slot order. */
function shot_labels(int $shotId): array
{
    $labels = [];
    foreach (shot_selection($shotId) as $assetId) {
        $asset = asset_get($assetId);
        if ($asset === null) {
            continue;
        }
        foreach (images_for($assetId) as $img) {
            $labels[] = Prompts::labelFor(
                (string) $asset['kind'],
                (string) $asset['name'],
                $asset['description'] === null ? null : (string) $asset['description'],
                (string) $img['role']
            );
        }
    }
    return $labels;
}

/** @return array{start:string, stop:string, labels:list<string>, notes:list<string>} */
function shot_preview(array $shot, array $project, ?string $styleOverride = null): array
{
    $labels = shot_labels((int) $shot['id']);
    $style = $styleOverride ?? ($project['style'] ?? null);
    $notes = [];

    $render = static function (string $body, array $imageLabels) use ($style, &$notes): string {
        if (trim($body) === '') {
            $notes[] = 'one of the texts is empty, so that frame cannot be generated yet';
            return '(empty)';
        }
        try {
            $r = Prompts::compose($body, $imageLabels, $style);
            foreach ($r['notes'] as $n) {
                $notes[] = $n;
            }
            return $r['prompt'];
        } catch (Throwable $e) {
            $notes[] = $e->getMessage();
            return '(could not compose)';
        }
    };

    $start = $render((string) $shot['start_text'], $labels);
    $stopLabels = $labels;
    $stopLabels[] = Prompts::START_FRAME_LABEL;
    $stop = $render((string) $shot['stop_text'], $stopLabels);

    if (count($labels) === 0) {
        $notes[] = 'no references selected: this would be a text-only generation';
    }

    return ['start' => $start, 'stop' => $stop, 'labels' => $labels, 'notes' => array_values(array_unique($notes))];
}

function require_project(int $id): array
{
    $p = project_get($id);
    if ($p === null) {
        http_response_code(404);
        exit('No such project.');
    }
    return $p;
}

function uploaded_files(string $field): array
{
    if (!isset($_FILES[$field]) || !is_array($_FILES[$field]['name'])) {
        return [];
    }
    $out = [];
    foreach ($_FILES[$field]['name'] as $i => $name) {
        if ((int) $_FILES[$field]['error'][$i] !== UPLOAD_ERR_OK) {
            continue;
        }
        $out[] = [
            'name' => (string) $name,
            'tmp' => (string) $_FILES[$field]['tmp_name'][$i],
            'type' => (string) $_FILES[$field]['type'][$i],
        ];
    }
    return $out;
}

/** Store one uploaded image and return [stored, width, height, hasAlpha] */
function accept_image(array $file): ?array
{
    $probe = image_probe($file['tmp']);
    if ($probe === null) {
        flash('Skipped ' . $file['name'] . ': not an image.', 'warnbadge');
        return null;
    }
    $mime = $probe['mime'] !== '' ? $probe['mime'] : $file['type'];
    $stored = store_media($file['tmp'], $mime);
    return [$stored, $probe['width'], $probe['height'], $probe['has_alpha']];
}

/* --------------------------------------------------------------- public bits */

if ($route === 'setup' || $route === 'setup.post') {
    if (has_password()) {
        redirect(url());
    }
    if ($route === 'setup' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        check_csrf();
        $pw = (string) ($_POST['password'] ?? '');
        $confirm = (string) ($_POST['confirm'] ?? '');
        if (strlen($pw) < 10) {
            flash('Password must be at least 10 characters.', 'warnbadge');
        } elseif ($pw !== $confirm) {
            flash('Passwords do not match.', 'warnbadge');
        } else {
            set_setting('password_hash', password_hash($pw, PASSWORD_DEFAULT));
            $_SESSION['auth'] = true;
            flash('Password set. This is your only login.');
            redirect(url());
        }
    }
    echo view_layout('Set a password', view_setup());
    exit;
}

if ($route === 'login' || $route === 'login.post') {
    $next = (string) ($_GET['next'] ?? $_POST['next'] ?? '');
    if ($route === 'login.post') {
        check_csrf();
        if (check_password((string) ($_POST['password'] ?? ''))) {
            $_SESSION['auth'] = true;
            session_regenerate_id(true);
            redirect($next !== '' && str_starts_with($next, 'index.php') ? $next : url());
        }
        usleep(400000); // slow down guessing
        flash('Wrong password.', 'warnbadge');
    }
    echo view_layout('Sign in', view_login($next));
    exit;
}

if ($route === 'logout') {
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        check_csrf();
    }
    $_SESSION = [];
    session_destroy();
    redirect(url());
}

require_auth();
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
}

/* -------------------------------------------------------------------- routes */

switch ($route) {
    case '':
        echo view_layout('Projects', view_dashboard(projects_all(), exports_list()));
        break;

    case 'project':
        $p = require_project(int_param('id'));
        $byKind = [];
        foreach (assets_for((int) $p['id']) as $a) {
            $byKind[$a['kind']][] = $a;
        }
        echo view_layout((string) $p['name'], view_project($p, $byKind, shots_for((int) $p['id'])));
        break;

    case 'project.create':
        $id = project_create(
            posted('name'),
            posted('style'),
            max(256, (int) posted('width', '1280')),
            max(256, (int) posted('height', '768'))
        );
        flash('Project created.');
        redirect(url('project', ['id' => $id]));
        break;

    case 'project.edit':
        $p = require_project(int_param('id'));
        echo view_layout('Project settings', view_project_edit($p), ['sub' => e((string) $p['name'])]);
        break;

    case 'project.save':
        $id = int_param('id');
        project_update($id, $_POST);
        flash('Saved.');
        redirect(url('project', ['id' => $id]));
        break;

    case 'project.delete':
        project_delete(int_param('id'));
        flash('Project deleted.');
        redirect(url());
        break;

    /* ------------------------------------------------------------------ assets */

    case 'asset':
        $a = asset_get(int_param('id'));
        if ($a === null) {
            http_response_code(404);
            exit('No such asset.');
        }
        $p = require_project((int) $a['project_id']);
        echo view_layout((string) $a['name'], view_asset($a, images_for((int) $a['id']), $p));
        break;

    case 'asset.create':
        $pid = int_param('project_id');
        $id = asset_create($pid, posted('kind', 'character'), posted('name'));
        flash('Asset added.');
        redirect(url('asset', ['id' => $id]));
        break;

    case 'asset.save':
        asset_update(int_param('id'), $_POST);
        flash('Saved.');
        redirect(url('asset', ['id' => int_param('id')]));
        break;

    case 'asset.delete':
        $a = asset_get(int_param('id'));
        if ($a !== null) {
            asset_delete((int) $a['id']);
            redirect(url('project', ['id' => $a['project_id']]));
        }
        redirect(url());
        break;

    case 'asset.image.add':
        $assetId = int_param('asset_id');
        $role = posted('role', 'other');
        $added = 0;
        foreach (uploaded_files('images') as $file) {
            $ok = accept_image($file);
            if ($ok === null) {
                continue;
            }
            [$stored, $w, $h, $alpha] = $ok;
            image_add($assetId, $role, $stored, $w, $h, $alpha);
            $added++;
        }
        flash($added . ' image(s) uploaded.');
        redirect(url('asset', ['id' => $assetId]));
        break;

    case 'asset.image.role':
        image_set_role(int_param('id'), posted('role', 'other'));
        $img = image_get(int_param('id'));
        redirect(url('asset', ['id' => $img['asset_id'] ?? 0]));
        break;

    case 'asset.image.fit':
        $id = int_param('id');
        $img = image_get($id);
        if ($img === null) {
            redirect(url());
        }
        $asset = asset_get((int) $img['asset_id']);
        $project = $asset === null ? null : project_get((int) $asset['project_id']);
        if ($project === null) {
            redirect(url());
        }
        $src = media_path((string) $img['file']);
        $mode = posted('mode', 'cover') === 'contain' ? 'contain' : 'cover';
        $tmp = image_fit($src, (int) $project['width'], (int) $project['height'], $mode);
        if ($tmp === null) {
            flash('Could not process that image (GD).', 'warnbadge');
        } else {
            $replaced = replace_media((string) $img['file'], $tmp);
            $probe = image_probe(media_path($replaced['file']));
            db()->prepare('UPDATE images SET file = ?, sha256 = ?, bytes = ?, width = ?, height = ?, has_alpha = ? WHERE id = ?')
                ->execute([
                    $replaced['file'],
                    $replaced['sha256'],
                    $replaced['bytes'],
                    $probe['width'] ?? 0,
                    $probe['height'] ?? 0,
                    ($probe['has_alpha'] ?? false) ? 1 : 0,
                    $id,
                ]);
            @unlink($tmp);
            flash('Fitted to ' . (int) $project['width'] . '×' . (int) $project['height'] . ' (' . $mode . ').');
        }
        redirect(url('asset', ['id' => (int) $img['asset_id']]));
        break;

    case 'asset.image.delete':
        $img = image_get(int_param('id'));
        if ($img !== null) {
            image_delete((int) $img['id']);
        }
        redirect(url('asset', ['id' => (int) ($img['asset_id'] ?? 0)]));
        break;

    /* ------------------------------------------------------------------- shots */

    case 'shot':
        $s = shot_get(int_param('id'));
        if ($s === null) {
            http_response_code(404);
            exit('No such shot.');
        }
        $p = require_project((int) $s['project_id']);
        echo view_layout(
            'Shot ' . $s['slug'],
            view_shot($s, $p, assets_for((int) $p['id']), shot_selection((int) $s['id']), shot_bookends((int) $s['id']), shot_preview($s, $p)),
            ['script' => base_url() . '/assets/app.js']
        );
        break;

    case 'shot.create':
        $pid = int_param('project_id');
        $id = shot_create($pid);
        redirect(url('shot', ['id' => $id]));
        break;

    case 'shot.save':
        $id = int_param('id');
        shot_update($id, $_POST);
        // Order: the hidden field set by the reorder buttons wins over POST order.
        $order = posted('asset_order');
        $ids = [];
        if ($order !== '') {
            foreach (explode(',', $order) as $part) {
                $ids[] = (int) $part;
            }
        } else {
            foreach (($_POST['assets'] ?? []) as $a) {
                $ids[] = (int) $a;
            }
        }
        shot_set_selection($id, array_values(array_filter(array_unique($ids))));
        flash('Shot saved.');
        redirect(url('shot', ['id' => $id]));
        break;

    case 'shot.delete':
        $s = shot_get(int_param('id'));
        $pid = $s['project_id'] ?? 0;
        shot_delete(int_param('id'));
        redirect(url('project', ['id' => (int) $pid]));
        break;

    case 'shot.duplicate':
        $new = shot_duplicate(int_param('id'));
        redirect(url('shot', ['id' => $new]));
        break;

    case 'shot.audio':
        $id = int_param('id');
        $file = $_FILES['audio'] ?? null;
        if (!is_array($file) || (int) ($file['error'] ?? 1) !== UPLOAD_ERR_OK) {
            flash('No audio uploaded.', 'warnbadge');
            redirect(url('shot', ['id' => $id]));
        }
        $mime = (string) ($file['type'] ?? '');
        $stored = store_media((string) $file['tmp_name'], $mime ?: 'audio/wav');
        $seconds = audio_duration(media_path($stored['file']), $stored['mime']);
        shot_audio_set($id, $stored, $seconds);
        flash('Audio attached' . ($seconds !== null ? ' (' . $seconds . ' s)' : '') . '.');
        redirect(url('shot', ['id' => $id]));
        break;

    case 'shot.audio.clear':
        shot_audio_clear(int_param('id'));
        redirect(url('shot', ['id' => int_param('id')]));
        break;

    case 'shot.bookend':
        $id = int_param('id');
        $count = 0;
        foreach (uploaded_files('frames') as $file) {
            $ok = accept_image($file);
            if ($ok === null) {
                continue;
            }
            [$stored, $w, $h] = $ok;
            $lower = strtolower($file['name']);
            $tag = str_contains($lower, 'stop') ? 'stop' : 'start';
            bookend_set($id, $tag, $stored, $w, $h);
            $count++;
        }
        flash($count . ' frame(s) attached.');
        redirect(url('shot', ['id' => $id]));
        break;

    case 'shot.bookend.clear':
        bookend_delete(int_param('id'), posted('tag', 'start'));
        redirect(url('shot', ['id' => int_param('id')]));
        break;

    /* ------------------------------------------------------------------ review */

    case 'review':
        $p = require_project(int_param('id'));
        $rows = [];
        foreach (shots_with_bookends((int) $p['id']) as $s) {
            $b = shot_bookends((int) $s['id']);
            if ($b['start'] === null || $b['stop'] === null) {
                $metrics = ['verdict' => 'missing', 'ssim' => null, 'mad' => null, 'hue' => null,
                    'reasons' => ['one of the two frames is missing']];
            } else {
                $metrics = rv_compare(media_path((string) $b['start']['file']), media_path((string) $b['stop']['file']));
            }
            $rows[] = ['shot' => $s, 'bookends' => $b, 'metrics' => $metrics, 'preview' => shot_preview($s, $p)];
        }
        echo view_layout('Review bookends', view_review($p, $rows));
        break;

    case 'review.decide':
        $id = int_param('id');
        $s = shot_get($id);
        if ($s !== null) {
            shot_update($id, array_merge($s, ['reviewed' => posted('decision', 'pending')]));
            flash('Marked ' . posted('decision', 'pending') . '.');
        }
        redirect(url('review', ['id' => (int) ($s['project_id'] ?? 0)]));
        break;

    /* ------------------------------------------------------------------ export */

    case 'export':
        $p = require_project(int_param('project'));
        $last = $_SESSION['export_result'] ?? null;
        unset($_SESSION['export_result']);
        echo view_layout(
            'Export',
            view_export($p, shots_for((int) $p['id']), exports_list(), $last, posted('style', (string) ($p['style'] ?? ''))),
            ['sub' => 'Build the payload the rented box expects: ' . e((string) $p['name'])]
        );
        break;

    case 'export.run':
        $pid = int_param('project');
        $ids = [];
        foreach (($_POST['shots'] ?? []) as $s) {
            $ids[] = (int) $s;
        }
        try {
            $result = export_build($pid, $ids === [] ? null : $ids, posted('style'));
            $_SESSION['export_result'] = $result;
            flash('Export built.');
        } catch (Throwable $ex) {
            flash('Export failed: ' . $ex->getMessage(), 'warnbadge');
        }
        redirect(url('export', ['project' => $pid]));
        break;

    case 'exports':
        echo view_layout('Exports', view_exports(exports_list()));
        break;

    case 'download':
        $path = export_download_path((string) ($_GET['id'] ?? ''), (string) ($_GET['path'] ?? ''));
        if ($path === null) {
            http_response_code(404);
            exit('Not found.');
        }
        $name = basename($path);
        $type = match (pathinfo($path, PATHINFO_EXTENSION)) {
            'gz' => 'application/gzip',
            'json', 'sha256', 'txt' => 'text/plain; charset=utf-8',
            default => 'application/octet-stream',
        };
        header('Content-Type: ' . $type);
        header('Content-Length: ' . (string) filesize($path));
        header('Content-Disposition: attachment; filename="' . $name . '"');
        header('X-Content-Type-Options: nosniff');
        readfile($path);
        break;

    /* ---------------------------------------------------- erase (TESTING ONLY) */

    case 'erase':
        echo view_layout(
            'Erase everything',
            view_erase(erase_stats()),
            ['sub' => 'Testing aid: clears all projects, references, shots and exports.']
        );
        break;

    case 'erase.confirm':
        if (strtoupper(trim(posted('confirm'))) !== 'ERASE') {
            flash('Type ERASE to confirm. Nothing was deleted.', 'warnbadge');
            redirect(url('erase'));
        }
        $keepPassword = isset($_POST['keep_password']);
        $result = erase_all($keepPassword);
        $b = $result['before'];
        flash(sprintf(
            'Erased %d project(s), %d asset(s), %d shot(s), %d file(s) (%s).',
            $b['projects'],
            $b['assets'],
            $b['shots'],
            $result['removed_files'],
            human_bytes($result['removed_bytes'])
        ));
        if ($keepPassword) {
            redirect(url());
        }
        // Password gone: start a fresh session so the setup screen appears.
        $_SESSION = [];
        session_destroy();
        redirect(url());
        break;

    /* ------------------------------------------------------------------ settings */

    case 'settings':
        $checks = array_map(static fn (array $c): array => [
            'label' => $c['label'], 'ok' => (bool) $c['ok'], 'detail' => (string) $c['detail'],
        ], health_checks());
        echo view_layout('Settings', view_settings($checks, (string) (setting('preview_width') ?? '1280'), (string) (setting('preview_height') ?? '768')));
        break;

    case 'settings.password':
        if (!check_password((string) ($_POST['current'] ?? ''))) {
            flash('Current password is wrong.', 'warnbadge');
            redirect(url('settings'));
        }
        $pw = (string) ($_POST['password'] ?? '');
        if (strlen($pw) < 10) {
            flash('New password must be at least 10 characters.', 'warnbadge');
            redirect(url('settings'));
        }
        set_setting('password_hash', password_hash($pw, PASSWORD_DEFAULT));
        flash('Password changed.');
        redirect(url('settings'));
        break;

    case 'settings.preview':
        set_setting('preview_width', posted('preview_width', '1280'));
        set_setting('preview_height', posted('preview_height', '768'));
        redirect(url('settings'));
        break;

    case 'health':
        echo view_layout('Health', view_health(health_checks(), health_env()));
        break;

    /* ------------------------------------------------------------------- media */

    case 'api.preview':
        $s = shot_get(int_param('shot'));
        if ($s === null) {
            http_response_code(404);
            header('Content-Type: application/json');
            echo '{"error":"no such shot"}';
            break;
        }
        $p = require_project((int) $s['project_id']);
        // The posted text is what the author is typing; everything else comes from the DB.
        $s['start_text'] = posted('start_text', (string) $s['start_text']);
        $s['stop_text'] = posted('stop_text', (string) $s['stop_text']);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(shot_preview($s, $p), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        break;

    case 'media':
        $v = (string) ($_GET['v'] ?? 'full');
        if (isset($_GET['image'])) {
            $img = image_get(int_param('image'));
            if ($img === null) {
                http_response_code(404);
                exit;
            }
            $path = $v === 'thumb' ? thumbnail((string) $img['file'], 480) : media_path((string) $img['file']);
            $type = $v === 'thumb' ? 'image/jpeg' : (string) $img['mime'];
        } elseif (isset($_GET['bookend'])) {
            $b = shot_bookends(int_param('bookend'));
            $tag = (string) ($_GET['tag'] ?? 'start');
            $row = $b[$tag === 'stop' ? 'stop' : 'start'] ?? null;
            if ($row === null) {
                http_response_code(404);
                exit;
            }
            $path = $v === 'thumb' ? thumbnail((string) $row['file'], 640) : media_path((string) $row['file']);
            $type = $v === 'thumb' ? 'image/jpeg' : (string) $row['mime'];
        } elseif (isset($_GET['audio'])) {
            $s = shot_get(int_param('audio'));
            if ($s === null || empty($s['audio_file'])) {
                http_response_code(404);
                exit;
            }
            $path = media_path((string) $s['audio_file']);
            $type = (string) ($s['audio_mime'] ?: 'audio/wav');
        } else {
            http_response_code(400);
            exit;
        }
        if ($path === null || !is_file($path)) {
            http_response_code(404);
            exit;
        }
        header('Content-Type: ' . $type);
        header('Content-Length: ' . (string) filesize($path));
        header('Cache-Control: private, max-age=300');
        header('X-Content-Type-Options: nosniff');
        readfile($path);
        break;

    default:
        http_response_code(404);
        echo view_layout('Not found', '<p>No such page. <a href="' . e(url()) . '">Back to projects</a>.</p>');
}
