<?php
/**
 * TESTING ONLY -- remove everything the studio has stored.
 *
 * This exists so test projects can be wiped without dropping the database by
 * hand. To delete the feature completely, remove:
 *
 *   - this file (src/erase.php) and its require in index.php
 *   - the 'erase' and 'erase.confirm' cases in index.php
 *   - view_erase() in src/views.php
 *   - the "Danger zone" card on the Settings page
 *   - the erase checks in tests/run.php and the final step of the smoke test
 *
 * Nothing else refers to it.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

/** What is currently stored, for the confirmation screen. */
function erase_stats(): array
{
    $db = db();
    $count = static fn (string $sql): int => (int) $db->query($sql)->fetchColumn();

    return [
        'projects' => $count('SELECT COUNT(*) FROM projects'),
        'assets' => $count('SELECT COUNT(*) FROM assets'),
        'images' => $count('SELECT COUNT(*) FROM images'),
        'shots' => $count('SELECT COUNT(*) FROM shots'),
        'bookends' => $count('SELECT COUNT(*) FROM bookends'),
        'media_files' => erase_count_files(QB_MEDIA),
        'media_bytes' => erase_size_files(QB_MEDIA),
        'exports' => count(glob(QB_EXPORTS . '/*', GLOB_ONLYDIR) ?: []),
    ];
}

/**
 * Delete every project, asset, image, shot, bookend, upload and export.
 *
 * The password is kept by default: wiping it would sign you out of a page you are
 * already on, which is confusing during testing. Pass keepPassword=false for a
 * factory reset, which also removes the login and returns to the setup screen.
 *
 * @return array{before:array, removed_files:int, removed_bytes:int, password_kept:bool}
 */
function erase_all(bool $keepPassword = true): array
{
    $before = erase_stats();
    $db = db();
    $db->beginTransaction();
    try {
        // Children first: explicit, so it does not depend on cascade settings.
        $db->exec('DELETE FROM bookends');
        $db->exec('DELETE FROM shot_assets');
        $db->exec('DELETE FROM shots');
        $db->exec('DELETE FROM images');
        $db->exec('DELETE FROM assets');
        $db->exec('DELETE FROM projects');
        if (!$keepPassword) {
            $db->exec("DELETE FROM settings WHERE key = 'password_hash'");
        }
        $db->commit();
    } catch (Throwable $e) {
        $db->rollBack();
        throw $e;
    }

    $files = 0;
    $bytes = 0;
    foreach ([QB_MEDIA, QB_THUMBS, QB_EXPORTS] as $dir) {
        [$n, $b] = erase_dir_contents($dir);
        $files += $n;
        $bytes += $b;
    }

    return [
        'before' => $before,
        'removed_files' => $files,
        'removed_bytes' => $bytes,
        'password_kept' => $keepPassword,
    ];
}

/** @return array{0:int, 1:int} files removed, bytes removed */
function erase_dir_contents(string $dir): array
{
    $dir = rtrim(str_replace('\\', '/', $dir), '/');
    if (!is_dir($dir)) {
        return [0, 0];
    }
    $files = 0;
    $bytes = 0;
    $walk = static function (string $path) use (&$walk, &$files, &$bytes): void {
        foreach (scandir($path) ?: [] as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $full = $path . '/' . $entry;
            if (is_dir($full)) {
                $walk($full);
                @rmdir($full); // only succeeds once empty
                continue;
            }
            // The directory guards must survive a wipe, or the next deploy serves data/.
            if ($entry === '.htaccess') {
                continue;
            }
            $size = @filesize($full);
            if (@unlink($full)) {
                $files++;
                $bytes += $size === false ? 0 : (int) $size;
            }
        }
    };
    $walk($dir);
    return [$files, $bytes];
}

function erase_count_files(string $dir): int
{
    if (!is_dir($dir)) {
        return 0;
    }
    $n = 0;
    foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS)) as $f) {
        if ($f->isFile() && $f->getFilename() !== '.htaccess') {
            $n++;
        }
    }
    return $n;
}

function erase_size_files(string $dir): int
{
    if (!is_dir($dir)) {
        return 0;
    }
    $bytes = 0;
    foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS)) as $f) {
        if ($f->isFile() && $f->getFilename() !== '.htaccess') {
            $bytes += (int) $f->getSize();
        }
    }
    return $bytes;
}
