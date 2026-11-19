<?php
/**
 * Qwen-bookend studio -- core: paths, config, database, session, auth helpers.
 *
 * Deliberately dependency-free: PDO/SQLite and GD are the only extensions used,
 * because the target is shared Apache hosting where composer is unavailable and
 * a vendor directory is just another thing to get out of sync. The Python CLI in
 * the parent repo remains the authority on the shot contract; this app writes
 * exactly the payload it expects (see src/export.php and tests/fixtures/).
 *
 * Every file under src/ and data/ is guarded by QB_BOOT so that a direct request
 * to one of them returns 404 rather than executing or leaking anything.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

const QB_APP = 'Qwen-bookend';
const QB_SCHEMA_VERSION = 3;

define('QB_ROOT', dirname(__DIR__));
define('QB_DATA', getenv('QB_DATA') ?: QB_ROOT . '/data');
define('QB_MEDIA', QB_DATA . '/media');
define('QB_THUMBS', QB_DATA . '/thumbs');
define('QB_EXPORTS', QB_DATA . '/exports');
define('QB_DB', QB_DATA . '/app.sqlite');

/** Escape for HTML. */
function e(?string $s): string
{
    return htmlspecialchars((string) $s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Build an internal URL. */
function url(string $route = '', array $params = []): string
{
    $q = $route === '' ? [] : ['r' => $route];
    $q += $params;
    return 'index.php' . ($q ? '?' . http_build_query($q) : '');
}

function redirect(string $where): never
{
    header('Location: ' . $where, true, 303);
    exit;
}

function now(): string
{
    return gmdate('Y-m-d\TH:i:s\Z');
}

function slugify(string $s): string
{
    $s = strtolower(trim($s));
    $s = preg_replace('/[^a-z0-9]+/', '_', $s) ?? '';
    $s = trim($s, '_');
    return $s === '' ? 'item' : substr($s, 0, 60);
}

function ensure_dirs(): void
{
    foreach ([QB_DATA, QB_MEDIA, QB_THUMBS, QB_EXPORTS] as $dir) {
        if (!is_dir($dir) && !@mkdir($dir, 0775, true) && !is_dir($dir)) {
            throw new RuntimeException("cannot create $dir");
        }
    }
}

/* ------------------------------------------------------------------ database */

function db(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }
    ensure_dirs();
    $pdo = new PDO('sqlite:' . QB_DB, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    $pdo->exec('PRAGMA journal_mode = WAL');
    $pdo->exec('PRAGMA foreign_keys = ON');
    $pdo->exec('PRAGMA busy_timeout = 5000');
    migrate($pdo);
    return $pdo;
}

function migrate(PDO $pdo): void
{
    $pdo->exec(<<<'SQL'
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            slug       TEXT NOT NULL,
            width      INTEGER NOT NULL DEFAULT 1280,
            height     INTEGER NOT NULL DEFAULT 768,
            style      TEXT,
            fps        INTEGER NOT NULL DEFAULT 24,
            notes      TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- character | prop | set
        CREATE TABLE IF NOT EXISTS assets (
            id          INTEGER PRIMARY KEY,
            project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            notes       TEXT,
            position    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        -- front | side | three_quarter | back | plate | detail | other
        CREATE TABLE IF NOT EXISTS images (
            id        INTEGER PRIMARY KEY,
            asset_id  INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            role      TEXT NOT NULL DEFAULT 'other',
            file      TEXT NOT NULL,
            mime      TEXT NOT NULL,
            width     INTEGER NOT NULL DEFAULT 0,
            height    INTEGER NOT NULL DEFAULT 0,
            bytes     INTEGER NOT NULL DEFAULT 0,
            sha256    TEXT NOT NULL DEFAULT '',
            has_alpha INTEGER NOT NULL DEFAULT 0,
            position  INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shots (
            id            INTEGER PRIMARY KEY,
            project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            slug          TEXT NOT NULL,
            position      INTEGER NOT NULL DEFAULT 0,
            start_text    TEXT NOT NULL DEFAULT '',
            stop_text     TEXT NOT NULL DEFAULT '',
            motion_prompt TEXT NOT NULL DEFAULT '',
            duration_s    REAL NOT NULL DEFAULT 0,
            audio_file    TEXT,
            audio_mime    TEXT,
            audio_bytes   INTEGER NOT NULL DEFAULT 0,
            audio_seconds REAL,
            reviewed      TEXT NOT NULL DEFAULT 'pending',
            review_note   TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shot_assets (
            shot_id  INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (shot_id, asset_id)
        );

        -- start.jpg / stop.jpg pulled back from the rented box
        CREATE TABLE IF NOT EXISTS bookends (
            id         INTEGER PRIMARY KEY,
            shot_id    INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            tag        TEXT NOT NULL,
            file       TEXT NOT NULL,
            mime       TEXT NOT NULL DEFAULT 'image/jpeg',
            width      INTEGER NOT NULL DEFAULT 0,
            height     INTEGER NOT NULL DEFAULT 0,
            bytes      INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id, kind, position);
        CREATE INDEX IF NOT EXISTS idx_images_asset   ON images(asset_id, position);
        CREATE INDEX IF NOT EXISTS idx_shots_project  ON shots(project_id, position);
        CREATE INDEX IF NOT EXISTS idx_bookends_shot  ON bookends(shot_id, tag);
        SQL);

    $pdo->prepare('INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)')
        ->execute(['schema_version', (string) QB_SCHEMA_VERSION]);
}

function setting(string $key, ?string $default = null): ?string
{
    $st = db()->prepare('SELECT value FROM settings WHERE key = ?');
    $st->execute([$key]);
    $v = $st->fetchColumn();
    return $v === false ? $default : (string) $v;
}

function set_setting(string $key, ?string $value): void
{
    if ($value === null) {
        db()->prepare('DELETE FROM settings WHERE key = ?')->execute([$key]);
        return;
    }
    db()->prepare('INSERT INTO settings(key, value) VALUES(?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value')
        ->execute([$key, $value]);
}

/* -------------------------------------------------------------- auth/session */

function session_start_safe(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'httponly' => true,
        'samesite' => 'Lax',
        'secure' => $https,
    ]);
    session_name('qbsid');
    session_start();
}

function has_password(): bool
{
    return (setting('password_hash') ?? '') !== '';
}

function check_password(string $password): bool
{
    $hash = setting('password_hash') ?? '';
    return $hash !== '' && password_verify($password, $hash);
}

function logged_in(): bool
{
    return ($_SESSION['auth'] ?? false) === true;
}

function require_auth(): void
{
    if (!has_password()) {
        redirect(url('setup'));
    }
    if (!logged_in()) {
        $back = $_SERVER['REQUEST_URI'] ?? url();
        redirect(url('login', ['next' => $back]));
    }
}

function csrf_token(): string
{
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['csrf'];
}

function csrf_field(): string
{
    return '<input type="hidden" name="_csrf" value="' . e(csrf_token()) . '">';
}

function check_csrf(): void
{
    $sent = (string) ($_POST['_csrf'] ?? '');
    if ($sent === '' || !hash_equals(csrf_token(), $sent)) {
        http_response_code(419);
        exit('CSRF token mismatch. Reload the page and try again.');
    }
}

/* ---------------------------------------------------------------- primitives */

/** Store an uploaded file under data/media, returning its stored metadata. */
function store_media(string $tmpPath, string $mime): array
{
    $sha = hash_file('sha256', $tmpPath);
    $ext = match ($mime) {
        'image/png' => 'png',
        'image/jpeg' => 'jpg',
        'image/webp' => 'webp',
        'image/gif' => 'gif',
        'audio/wav', 'audio/x-wav', 'audio/wave' => 'wav',
        'audio/mpeg' => 'mp3',
        'audio/ogg' => 'ogg',
        'application/zip' => 'zip',
        default => 'bin',
    };
    $sub = substr($sha, 0, 2);
    $dir = QB_MEDIA . '/' . $sub;
    if (!is_dir($dir)) {
        @mkdir($dir, 0775, true);
    }
    $rel = $sub . '/' . $sha . '.' . $ext;
    $dest = QB_MEDIA . '/' . $rel;
    if (!is_file($dest)) {
        if (!move_uploaded_file($tmpPath, $dest) && !@copy($tmpPath, $dest)) {
            throw new RuntimeException('could not store upload');
        }
    }
    return ['file' => $rel, 'sha256' => $sha, 'bytes' => filesize($dest) ?: 0, 'mime' => $mime];
}

function media_path(string $rel): string
{
    return QB_MEDIA . '/' . $rel;
}

/** Replace a media file's contents in place (used by crop/pad). */
function replace_media(string $rel, string $newTmp): array
{
    $dest = media_path($rel);
    $sha = hash_file('sha256', $newTmp);
    $ext = pathinfo($dest, PATHINFO_EXTENSION);
    $sub = substr($sha, 0, 2);
    $newRel = $sub . '/' . $sha . '.' . $ext;
    $newDest = QB_MEDIA . '/' . $newRel;
    if (!is_dir(dirname($newDest))) {
        @mkdir(dirname($newDest), 0775, true);
    }
    if (!@copy($newTmp, $newDest)) {
        throw new RuntimeException('could not store rewritten image');
    }
    @unlink($dest);
    return ['file' => $newRel, 'sha256' => $sha, 'bytes' => filesize($newDest) ?: 0];
}

function human_bytes(int $bytes): string
{
    if ($bytes < 1024) {
        return $bytes . ' B';
    }
    if ($bytes < 1024 * 1024) {
        return round($bytes / 1024, 1) . ' KB';
    }
    return round($bytes / 1024 / 1024, 2) . ' MB';
}

function posted(string $key, string $default = ''): string
{
    $v = $_POST[$key] ?? $default;
    return is_string($v) ? trim($v) : $default;
}

function int_param(string $key, int $default = 0): int
{
    $v = $_GET[$key] ?? $_POST[$key] ?? null;
    return is_numeric($v) ? (int) $v : $default;
}

function flash(string $message, string $kind = 'ok'): void
{
    $_SESSION['flash'][] = ['kind' => $kind, 'message' => $message];
}

function take_flashes(): array
{
    $f = $_SESSION['flash'] ?? [];
    unset($_SESSION['flash']);
    return $f;
}
